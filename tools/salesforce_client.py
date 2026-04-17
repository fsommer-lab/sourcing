import logging
from typing import Optional

from simple_salesforce import Salesforce, SalesforceAuthenticationFailed

from config import (
    SALESFORCE_DOMAIN,
    SALESFORCE_PASSWORD,
    SALESFORCE_SECURITY_TOKEN,
    SALESFORCE_USERNAME,
)
from models import Company

logger = logging.getLogger(__name__)


class SalesforceClient:
    def __init__(self):
        self._sf: Optional[Salesforce] = None

    def _connect(self) -> Optional[Salesforce]:
        if self._sf:
            return self._sf
        if not all([SALESFORCE_USERNAME, SALESFORCE_PASSWORD, SALESFORCE_SECURITY_TOKEN]):
            logger.warning("Salesforce credentials incomplete — CRM dedup disabled")
            return None
        try:
            self._sf = Salesforce(
                username=SALESFORCE_USERNAME,
                password=SALESFORCE_PASSWORD,
                security_token=SALESFORCE_SECURITY_TOKEN,
                domain=SALESFORCE_DOMAIN,
            )
            logger.info("Connected to Salesforce (%s)", SALESFORCE_DOMAIN)
            return self._sf
        except SalesforceAuthenticationFailed as e:
            logger.error("Salesforce auth failed: %s", e)
            return None

    def _find(self, company_name: str) -> Optional[str]:
        """Return Salesforce record ID if company exists as Account or Lead."""
        sf = self._connect()
        if not sf:
            return None

        # Escape single quotes to avoid SOQL injection
        safe = company_name.replace("'", "\\'")

        for obj, field in [("Account", "Name"), ("Lead", "Company")]:
            try:
                result = sf.query(
                    f"SELECT Id FROM {obj} WHERE {field} LIKE '%{safe}%' LIMIT 1"
                )
                if result["records"]:
                    return result["records"][0]["Id"]
            except Exception as e:
                logger.warning("Salesforce query error (%s): %s", obj, e)

        return None

    def filter_new(self, companies: list[Company]) -> list[Company]:
        """Mark companies already in CRM and return only the net-new ones."""
        new: list[Company] = []
        for company in companies:
            sf_id = self._find(company.name)
            if sf_id:
                company.in_salesforce = True
                company.salesforce_id = sf_id
                logger.info("Already in Salesforce: %s (%s)", company.name, sf_id)
            else:
                new.append(company)
        return new

    def add_lead(self, company: Company) -> Optional[str]:
        """Create a Lead record in Salesforce for a net-new company."""
        sf = self._connect()
        if not sf:
            return None

        payload: dict = {
            "LastName": company.name,
            "Company": company.name,
            "LeadSource": "AI Sourcing Agent",
            "Country": company.country,
            "Website": company.website,
            "Description": _truncate(company.description, 32000),
        }

        if company.headcount:
            payload["NumberOfEmployees"] = company.headcount

        # Optional custom fields — uncomment after your SF admin creates them:
        # payload["Total_Funding_EUR__c"] = company.total_funding_eur
        # payload["Last_Round_Type__c"] = company.last_round_type
        # payload["Sourcing_Score__c"] = company.score
        # payload["Data_Source__c"] = company.source

        # Remove None values — Salesforce rejects explicit nulls on create
        payload = {k: v for k, v in payload.items() if v is not None}

        try:
            result = sf.Lead.create(payload)
            sf_id = result["id"]
            logger.info("Salesforce lead created: %s → %s", company.name, sf_id)
            company.salesforce_id = sf_id
            return sf_id
        except Exception as e:
            logger.error("Failed to create Salesforce lead for %s: %s", company.name, e)
            return None


def _truncate(text: str, max_len: int) -> str:
    return text[:max_len] if text else ""

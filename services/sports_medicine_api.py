"""
CHC MDS API service for sports medicine bookings.
Separate system from Cerba ambulatory API — different base URL, JWT auth, endpoints.

API Spec v1.1 (Oct 2025). Field names used as-is from official docs.

Endpoints:
- POST Login/Access → JWT (TokenJWT)
- GET group/GetList → regions [{ID, Name}]
- GET Sedi/GetList → facilities [{ID, Name, PossAGO, Note}]
- GET Sedi/FirstValidateType → {Validate, Note}
- GET Sedi/ValidateType → {Validate, Tipo_AB, Note}
- GET Sport/GetList → [{ID_Sport, NameIT, NameEN, Tipologia}]
- GET Slot/Find → [{Slot_ID, Slot_Date}]
- POST Slot/Lock → {Esito}
- GET Slot/Status → {Esito} (0=not booked, 1=in progress, 3=confirmed, etc)
- GET Slot/CalculatePrice → {has_free, price, has_price_cerba_card, price_cerba_card, ...}
- POST Slot/Insert → {Esito}
"""

import requests
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from loguru import logger

from config.settings import settings


class MDSAPIError(Exception):
    """Custom exception for MDS API errors"""
    pass


# Static region → group ID mapping (from group/GetList).
# Avoids an API call per request (~700ms saved). These rarely change.
REGION_GROUP_MAP = {
    "emilia romagna": {"ID": "y3Y7Cn", "Name": "EMILIA ROMAGNA"},
    "lazio": {"ID": "f4XHw6", "Name": "LAZIO"},
    "lombardia": {"ID": "i8P7Qc", "Name": "LOMBARDIA"},
    "piemonte": {"ID": "Le94Ht", "Name": "PIEMONTE"},
    "trentino alto adige": {"ID": "We5c8T", "Name": "TRENTINO ALTO ADIGE"},
    "trentino-alto adige": {"ID": "We5c8T", "Name": "TRENTINO ALTO ADIGE"},
    "veneto": {"ID": "Zk96Bm", "Name": "VENETO"},
}

# Stage URL for endpoints not yet deployed to production
MDS_STAGE_URL = "https://vsuserstage.cerbahealthcare.it/api/endpoint"


class MDSAPIService:
    """Service for CHC MDS sports medicine API"""

    def __init__(self):
        self._token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None

    @property
    def base_url(self) -> str:
        return settings.mds_api_config["base_url"]

    @property
    def username(self) -> str:
        return settings.mds_api_config["username"]

    @property
    def password(self) -> str:
        return settings.mds_api_config["password"]

    # ---- Auth ----

    def _get_token(self) -> str:
        """Get valid JWT token, refreshing if needed."""
        if self._token and self._token_expiry and datetime.now() < self._token_expiry:
            return self._token
        return self._refresh_token()

    def _refresh_token(self) -> str:
        """POST Login/Access → TokenJWT"""
        url = f"{self.base_url}/Login/Access"
        payload = {"Username": self.username, "Password": self.password}

        logger.info("🔑 MDS API: Refreshing JWT token")
        try:
            resp = requests.post(url, json=payload, timeout=10)

            # 299 = business error (wrong credentials etc)
            if resp.status_code == 299:
                data = resp.json()
                raise MDSAPIError(f"Auth failed: {data.get('Error_Message', 'Unknown')}")

            resp.raise_for_status()
            data = resp.json()

            token = data.get("TokenJWT")
            if not token:
                raise MDSAPIError(f"No TokenJWT in response: {data}")

            self._token = token
            # Default 1h expiry with 5min buffer
            self._token_expiry = datetime.now() + timedelta(minutes=55)
            logger.info(f"MDS API: Token refreshed, expires ~{self._token_expiry}")
            return self._token

        except requests.exceptions.RequestException as e:
            logger.error(f"MDS API: Token request failed: {e}")
            raise MDSAPIError(f"Authentication failed: {e}")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json"
        }

    def _get(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        """Make authenticated GET request with auto-retry on 401.
        MDS API accepts params as JSON body for GET requests (per API spec v1.1).
        """
        url = f"{self.base_url}/{endpoint}"
        logger.info(f"📡 MDS API GET: {endpoint} | params={params}")

        try:
            resp = requests.get(url, headers=self._headers(), json=params, timeout=15)

            # Auto-refresh on 401
            if resp.status_code == 401:
                logger.warning("MDS API: 401 — refreshing token and retrying")
                self._token = None
                resp = requests.get(url, headers=self._headers(), json=params, timeout=15)

            # 299 = business error
            if resp.status_code == 299:
                data = resp.json()
                error_code = data.get("Error_Code", "unknown")
                error_msg = data.get("Error_Message", "Unknown error")
                logger.warning(f"MDS API GET {endpoint}: 299/{error_code} — {error_msg}")
                return data  # Return error payload for handler to process

            # 500 = server error — return error payload if JSON, else raise
            if resp.status_code == 500:
                try:
                    data = resp.json()
                    logger.error(f"MDS API GET {endpoint}: 500 — {data}")
                    return data  # Return for handler to process
                except Exception:
                    logger.error(f"MDS API GET {endpoint}: 500 — {resp.text[:300]}")
                    raise MDSAPIError(f"GET {endpoint} server error: {resp.status_code}")

            # 404 = endpoint not found (e.g. CalculatePrice not deployed) — return error data
            if resp.status_code == 404:
                try:
                    data = resp.json()
                    logger.warning(f"MDS API GET {endpoint}: 404 — {data.get('Message', 'Not found')}")
                    return data
                except Exception:
                    logger.warning(f"MDS API GET {endpoint}: 404")
                    return {"Error_Code": 404, "Error_Message": "Endpoint not found"}

            if resp.status_code >= 400:
                logger.error(f"MDS API GET {endpoint}: {resp.status_code} — {resp.text[:300]}")
                raise MDSAPIError(f"GET {endpoint} failed: {resp.status_code}")

            return resp.json()

        except requests.exceptions.RequestException as e:
            logger.error(f"MDS API GET {endpoint} error: {e}")
            raise MDSAPIError(f"GET {endpoint} failed: {e}")

    def _post(self, endpoint: str, payload: Optional[Dict] = None) -> Any:
        """Make authenticated POST request with auto-retry on 401."""
        url = f"{self.base_url}/{endpoint}"
        logger.info(f"📡 MDS API POST: {endpoint}")

        try:
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=15)

            if resp.status_code == 401:
                logger.warning("MDS API: 401 — refreshing token and retrying")
                self._token = None
                resp = requests.post(url, headers=self._headers(), json=payload, timeout=15)

            # 299 = business error
            if resp.status_code == 299:
                data = resp.json()
                error_code = data.get("Error_Code", "unknown")
                error_msg = data.get("Error_Message", "Unknown error")
                logger.warning(f"MDS API POST {endpoint}: 299/{error_code} — {error_msg}")
                return data

            # 500 = server error — return error payload if JSON, else raise
            if resp.status_code == 500:
                try:
                    data = resp.json()
                    logger.error(f"MDS API POST {endpoint}: 500 — {data}")
                    return data
                except Exception:
                    logger.error(f"MDS API POST {endpoint}: 500 — {resp.text[:300]}")
                    raise MDSAPIError(f"POST {endpoint} server error: {resp.status_code}")

            if resp.status_code >= 400:
                logger.error(f"MDS API POST {endpoint}: {resp.status_code} — {resp.text[:300]}")
                raise MDSAPIError(f"POST {endpoint} failed: {resp.status_code}")

            return resp.json()

        except requests.exceptions.RequestException as e:
            logger.error(f"MDS API POST {endpoint} error: {e}")
            raise MDSAPIError(f"POST {endpoint} failed: {e}")

    # ---- Group / Region ----

    def get_groups(self) -> List[Dict[str, Any]]:
        """GET group/GetList → [{ID, Name}]"""
        return self._get("group/GetList")

    def find_group_by_region(self, region_name: str) -> Optional[Dict[str, Any]]:
        """Match region name to group ID using local static mapping.
        No API call needed — regions are stable (6 total).
        Falls back to API call if local map has no match.
        """
        region_lower = region_name.lower().strip()

        # Local exact match (covers 99% of cases)
        if region_lower in REGION_GROUP_MAP:
            group = REGION_GROUP_MAP[region_lower]
            logger.info(f"📍 Region matched locally: {region_name} → {group['ID']} ({group['Name']})")
            return group

        # Fallback: API call (in case new regions are added)
        logger.info(f"📍 Region '{region_name}' not in local map, calling group/GetList")
        groups = self.get_groups()
        if isinstance(groups, dict) and "Error_Code" in groups:
            return None
        for group in groups:
            name = (group.get("Name") or "").lower()
            if region_lower == name or region_lower in name:
                return group
        return None

    # ---- Facilities ----

    def get_facilities(self, id_group: str, lang: str = "it") -> List[Dict[str, Any]]:
        """GET Sedi/GetList → [{ID, Name, PossAGO, Note}]"""
        return self._get("Sedi/GetList", {"ID_Group": id_group, "Lang": lang})

    def first_validate_type(
        self, id_group: str, id_sede: int, ago: bool, lang: str = "it"
    ) -> Dict[str, Any]:
        """GET Sedi/FirstValidateType → {Validate, Note}
        Informational pre-check — currently always returns Validate=true.
        """
        return self._get("Sedi/FirstValidateType", {
            "ID_Group": id_group,
            "ID_Sede": id_sede,
            "AGO": ago,
            "Lang": lang
        })

    def validate_type(
        self, id_group: str, id_sede: int, ago: bool, b1: bool,
        sport: str = "", lang: str = "it"
    ) -> Dict[str, Any]:
        """GET Sedi/ValidateType → {Validate, Tipo_AB, Note}
        Sport field MUST always be present (empty string for non-agonistic).
        Server returns 500 NullReferenceException if Sport is omitted entirely.
        """
        return self._get("Sedi/ValidateType", {
            "ID_Group": id_group,
            "ID_Sede": id_sede,
            "AGO": ago,
            "B1": b1,
            "Sport": sport,
            "Lang": lang
        })

    # ---- Sports (Agonistic flow) ----

    def get_sports(self) -> List[Dict[str, Any]]:
        """GET Sport/GetList → [{ID_Sport, NameIT, NameEN, Tipologia}]"""
        return self._get("Sport/GetList")

    # ---- Slots ----

    def find_slots(
        self,
        id_sede: int,
        tipo_ab: int,
        date_rif: str,
        days: int = 30,
        n_slots: int = 10
    ) -> List[Dict[str, Any]]:
        """GET Slot/Find → [{Slot_ID, Slot_Date}]
        date_rif format: "YYYY/MM/DD"
        Slot_Date format: "YYYY/MM/DD HH24:mm"
        """
        return self._get("Slot/Find", {
            "ID_Sede": id_sede,
            "Tipo_AB": tipo_ab,
            "DateRif": date_rif,
            "Days": days,
            "N_Slots": n_slots
        })

    def lock_slot(self, id_group: str, id_sede: int, slot_id: int) -> Dict[str, Any]:
        """POST Slot/Lock → {Esito: "Ok"}
        Locks slot for 10 minutes.
        Error 299/10: slot no longer available.
        """
        return self._post("Slot/Lock", {
            "ID_Group": id_group,
            "ID_Sede": id_sede,
            "Slot_ID": slot_id
        })

    def get_slot_status(self, id_group: str, id_sede: int, slot_id: int) -> Dict[str, Any]:
        """GET Slot/Status → {Esito}
        Esito values: 0=not booked, 1=in progress, 2=not confirmed,
        3=confirmed, 5=cancelled, 6=deleted, 10/11/12=visit done
        """
        return self._get("Slot/Status", {
            "ID_Group": id_group,
            "ID_Sede": id_sede,
            "Slot_ID": slot_id
        })

    def calculate_price(
        self,
        id_group: str,
        tipo_ab: int,
        b1: bool,
        sex: str,
        dt_nascita: str,
        id_sport: int = 0,
        id_soc_sportiva: int = 0
    ) -> Dict[str, Any]:
        """GET Slot/CalculatePrice → {has_free, price, has_price_cerba_card, price_cerba_card, ...}
        dt_nascita format: "YYYY/MM/DD", sex: "M" or "F"

        NOTE: Uses stage URL — endpoint not deployed to production yet.
        TODO: Switch to self.base_url once client deploys to production.
        """
        params = {
            "ID_Group": id_group,
            "Tipo_AB": tipo_ab,
            "B1": b1,
            "Sex": sex,
            "Dt_Nascita": dt_nascita,
        }
        if id_sport:
            params["ID_Sport"] = id_sport
        if id_soc_sportiva:
            params["ID_SocSportiva"] = id_soc_sportiva

        # Use stage URL since CalculatePrice is not on production yet
        url = f"{MDS_STAGE_URL}/Slot/CalculatePrice"
        logger.info(f"📡 MDS API GET (stage): Slot/CalculatePrice | params={params}")
        try:
            resp = requests.get(url, headers=self._headers(), json=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"MDS CalculatePrice (stage): {resp.status_code} — {resp.text[:200]}")
            return {"Error_Code": resp.status_code, "Error_Message": resp.text[:200]}
        except requests.exceptions.RequestException as e:
            logger.warning(f"MDS CalculatePrice (stage) error: {e}")
            return {"Error_Code": -1, "Error_Message": str(e)}

    # ---- Booking ----

    def insert_booking(
        self,
        id_group: str,
        id_sede: int,
        tipo_ab: int,
        slot_id: int,
        slot_date: str,
        b1: bool,
        nome: str,
        cognome: str,
        sex: str,
        dt_nascita: str,
        telefono: str,
        email: str,
        consenso_promemoria: bool = True,
        cod_fiscale: str = "",
        luogo_nascita: str = "",
        indirizzo: str = "",
        citta: str = "",
        id_sport: int = None,
        sport: str = None,
        id_soc_sportiva: int = None,
        nome_soc_sportiva: str = None,
        lang: str = "it"
    ) -> Dict[str, Any]:
        """POST Slot/Insert → {Esito: "Ok"}
        Error codes: 299/8 = age/gender restriction, 299/9 = slot taken
        """
        payload = {
            "ID_Group": id_group,
            "ID_Sede": id_sede,
            "Tipo_AB": tipo_ab,
            "Slot_ID": slot_id,
            "Slot_Date": slot_date,
            "B1": b1,
            "Nome": nome,
            "Cognome": cognome,
            "Sex": sex,
            "Dt_Nascita": dt_nascita,
            "Telefono": telefono,
            "Email": email,
            "Consenso_Promemoria": consenso_promemoria,
            "Lang": lang
        }
        # Optional fields
        if cod_fiscale:
            payload["CodFiscale"] = cod_fiscale
        if luogo_nascita:
            payload["Luogo_Nascita"] = luogo_nascita
        if indirizzo:
            payload["Indirizzo"] = indirizzo
        if citta:
            payload["Citta"] = citta
        # Agonistic-specific
        if id_sport:
            payload["ID_Sport"] = id_sport
        if sport:
            payload["Sport"] = sport
        if id_soc_sportiva:
            payload["ID_SocSportiva"] = id_soc_sportiva
        if nome_soc_sportiva:
            payload["Nome_SocSportiva"] = nome_soc_sportiva

        return self._post("Slot/Insert", payload)

    # ---- Sports Clubs (Agonistic) ----

    def get_sports_clubs(self, id_group: str, sport_it: str = None) -> List[Dict[str, Any]]:
        """GET dir/GetList → [{ID, Nome}]
        Optional sport_it filter to get clubs for specific sport.
        """
        params = {"ID_Group": id_group}
        if sport_it:
            params["SportIT"] = sport_it
        return self._get("dir/GetList", params)

    def clear_token(self):
        """Clear stored token (for testing or forced refresh)."""
        self._token = None
        self._token_expiry = None
        logger.info("MDS API: Token cleared")


# Global instance
mds_api = MDSAPIService()

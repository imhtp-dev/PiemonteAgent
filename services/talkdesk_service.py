import base64
import requests
import os
from loguru import logger
import json
from typing import Optional, Dict, Any




TALKDESK_CONFIG = {
    "client_id": os.getenv("TALKDESK_CLIENT_ID", ""),
    "client_secret": os.getenv("TALKDESK_CLIENT_SECRET", ""),
    "account_name": os.getenv("TALKDESK_ACCOUNT_NAME", "cerba"),
    "api_url": os.getenv("TALKDESK_API_URL", "https://api.talkdeskapp.eu/interaction-custom-fields")
}

def get_talkdesk_access_token() -> Optional[str]:
        """
        Ottieni un access token per Talkdesk
        
        Returns:
            Optional[str]: Access token o None se errore
        """
        try:
            credentials = f"{TALKDESK_CONFIG['client_id']}:{TALKDESK_CONFIG['client_secret']}"
            encoded_credentials = base64.b64encode(credentials.encode()).decode()
            
            token_url = f"https://{TALKDESK_CONFIG['account_name']}.talkdeskid.eu/oauth/token"
            
            headers = {
                "Authorization": f"Basic {encoded_credentials}",
                "Content-Type": "application/x-www-form-urlencoded"
            }
            
            payload = {"grant_type": "client_credentials"}
            
            response = requests.post(token_url, headers=headers, data=payload, timeout=30)
            
            if response.status_code == 200:
                token_data = response.json()
                return token_data["access_token"]
            else:
                logger.error(f"Errore ottenimento token Talkdesk: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Errore connessione Talkdesk per token: {e}")
            return None
        


def send_to_talkdesk(call_data: Dict[str, Any]) -> bool:
        """
        Invia i dati a Talkdesk
        
        Args:
            call_data (Dict[str, Any]): Dati della chiamata analizzata
            
        Returns:
            bool: True se invio riuscito
        """
        try:
            access_token = get_talkdesk_access_token()
            if not access_token:
                logger.error("Impossibile ottenere access token Talkdesk")
                return False
            
            headers = {
                "accept": "application/json",
                "content-type": "application/json",
                "Authorization": f"Bearer {access_token}"
            }
            
            payload = {
                "interaction_id": call_data["interaction_id"],
                "text_field_1": "completed_by_voice_agent",  # Hard coded come richiesto
                "text_field_2": call_data["sentiment"],
                "text_field_3": call_data["service"],  # Sempre "2|2|5"
                "text_field_4": call_data["summary"],
                "numeric_field_1": int(call_data["duration_seconds"]),
                "numeric_field_2": 1
            }
            
            logger.info(f"=== INVIO TALKDESK per interaction_id: {call_data['interaction_id']} ===")
            logger.info(f"Payload completo: {json.dumps(payload, indent=2, ensure_ascii=False)}")
            
            response = requests.post(
                TALKDESK_CONFIG["api_url"], 
                json=payload, 
                headers=headers, 
                timeout=30
            )
            
            logger.info(f"=== RISPOSTA TALKDESK ===")
            logger.info(f"Status Code: {response.status_code}")
            logger.info(f"Response Headers: {dict(response.headers)}")
            logger.info(f"Response Text: {response.text}")
            
            if response.status_code in [200, 201, 204]:
                logger.info(f"[SUCCESS] Dati inviati con successo a Talkdesk per interaction_id: {call_data['interaction_id']}")
                logger.info(f"[CONFIRM] text_field_1=completed_by_voice_agent, text_field_2={call_data['sentiment']}, text_field_3={call_data['service']}")
                return True
            else:
                logger.error(f"[ERROR] Errore Talkdesk per interaction_id {call_data['interaction_id']}: Status {response.status_code}")
                logger.error(f"[ERROR] Response: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Errore invio Talkdesk per interaction_id {call_data['interaction_id']}: {e}")
            return False
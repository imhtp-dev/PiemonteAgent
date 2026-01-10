"""

"""
ambiente="prod"
import requests
import json
from services.amb_json_flow_eng import recupera_amb_json_flow

def get_token():
    url = 'https://cerbahc.auth.eu-central-1.amazoncognito.com/oauth2/token'
    client_id = '732bjl1ih32jdk3qjcq7dej1tp'
    client_secret = 'c8vst12at03p7d197648h1apktkuv61f8d83qtg3jdbh0nntf8'
    payload = {
    "client_id": client_id,
    "client_secret": client_secret,
    "grant_type": "client_credentials",  
    "scope": "voila/api"
}
    headers = {
    "Content-Type": "application/x-www-form-urlencoded"
}

    response = requests.post(url, data=payload, headers=headers)
    
    token=""
    if response.status_code == 200:
        token = response.json()['access_token']
    else:
        print(f'Errore durante la richiesta: {response.status_code} - {response.text}')
    return token



def aggiungi_unico(lista, valore, prestazione_principale):
    """
    Aggiunge un valore alla lista solo se:
    1. Il valore non è già presente nella lista (evita duplicati)
    2. Il valore non è uguale alla prestazione principale
    """
    # Aggiunge solo se il valore non è già presente e non è la prestazione principale
    if valore not in lista and valore != prestazione_principale:
        lista.append(valore)
def genera_flow(hc_uuid,medical_exam_id):
    token=get_token()
    
    api_url = f'https://3z0xh9v1f4.execute-api.eu-south-1.amazonaws.com/{ambiente}/amb/health-service/{medical_exam_id}'

    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }

    
    request_data = {
        'gender':'m',
        'date_of_birth':'1990-08-11',
        'health_centers':hc_uuid, # I'll pass you a multi-health center so that you can recover the services that can be performed in these centers.
    }
    response = requests.get(api_url,headers=headers,params=request_data)#,params=request_data
    #print(request_data,medical_exam_id)
    if response.status_code == 200:
        data = response.json()
        formatted_json = json.dumps(data, indent=4)
        #print(formatted_json)
        #print(data)
        resp=data
        print(resp)
        uuid = data['uuid']
        name = data['name']
        health_service_code = data['health_service_code']
        code = data['health_service_code']
        ##
        requires_prescription = data['requires_prescription']
        requires_recontact = data['requires_recontact']
        requires_preliminary_visit = data['requires_preliminary_visit']
        follow_up = data['follow_up']
        medical_examination = data['medical_examination']
        lab_special_health_service = data['lab_special_health_service']
        lab_required_health_service = data['lab_required_health_service']
        bundle = data['bundle']
        checkup = data['checkup']
        bundle_health_services = data['bundle_health_services']
        checkup_health_services = data['checkup_health_services']
        included_bundles = data['included_bundles']
        follow_up_health_services = data['follow_up_health_services']
        popular = data['popular']
        requires_medical_device = data['requires_medical_device']
        medical_device = data['medical_device']
        ##Accessorie Array
        accessorie=[]
        accessorie_uuid=[]
        accessorie_code=[]
        ##Prima Visita Array
        prima_visita=[]
        prima_visita_uuid=[]
        prima_visita_code=[]
        ##Visita Specialistica Array
        visita_specialista=[]
        visita_specialista_uuid=[]
        visita_specialista_code=[]
        ##Commento Array
        commento=[]
        commento_uuid=[]
        commento_code=[]
        #print(data)
        for relation in data['health_service_relations']:
            
            relation_uuid = relation['uuid']
            relation_type_uuid = relation['health_service_relation_type']['uuid']
            relation_type_name = relation['health_service_relation_type']['name']
            relation_type_label = relation['health_service_relation_type']['label']
            previous_service_uuid = relation['previous_health_service']['uuid']
            previous_service_name = relation['previous_health_service']['name']
            previous_service_code = relation['previous_health_service']['health_service_code']
            following_service_uuid = relation['following_health_service']['uuid']
            following_service_name = relation['following_health_service']['name']
            following_service_code = relation['following_health_service']['health_service_code']

            #print(following_service_name,relation_type_label)
           
            
            if(relation_type_label=="Accessoria"):
                #Health Service Name
                #print(following_service_name,following_service_uuid,following_service_code)
                aggiungi_unico(accessorie,previous_service_name,name)
                aggiungi_unico(accessorie,following_service_name,name)
                #Health Service UUID
                aggiungi_unico(accessorie_uuid,previous_service_uuid,uuid)
                aggiungi_unico(accessorie_uuid,following_service_uuid,uuid)
                #Health Service Code
                aggiungi_unico(accessorie_code,previous_service_code,code)
                aggiungi_unico(accessorie_code,following_service_code,code)
            if(relation_type_label=="Prescrizione"):####
                #Health Service Name
                #print(previous_service_name,following_service_name)
                #print(previous_service_name,following_service_uuid,following_service_code)
                aggiungi_unico(prima_visita,previous_service_name,name)
                aggiungi_unico(prima_visita,following_service_name,name)
                #Health Service UUID
                aggiungi_unico(prima_visita_uuid,previous_service_uuid,uuid)
                aggiungi_unico(prima_visita_uuid,following_service_uuid,uuid)
                #Health Service Code
                aggiungi_unico(prima_visita_code,previous_service_code,code)
                aggiungi_unico(prima_visita_code,following_service_code,code)
            if(relation_type_label=="Visita Preliminare"):
                #print(previous_service_name,following_service_uuid,following_service_code)
                aggiungi_unico(visita_specialista,previous_service_name,name)
                aggiungi_unico(visita_specialista,following_service_name,name)
                #Health Service UUID
                aggiungi_unico(visita_specialista_uuid,previous_service_uuid,uuid)
                aggiungi_unico(visita_specialista_uuid,following_service_uuid,uuid)
                #Health Service Code
                aggiungi_unico(visita_specialista_code,previous_service_code,code)
                aggiungi_unico(visita_specialista_code,following_service_code,code)
            if(relation_type_label=="Commento"):
                aggiungi_unico(commento,previous_service_name,name)
                aggiungi_unico(commento,following_service_name,name)
                #Health Service UUID
                aggiungi_unico(commento_uuid,previous_service_uuid,uuid)
                aggiungi_unico(commento_uuid,following_service_uuid,uuid)
                #Health Service Code
                aggiungi_unico(commento_code,previous_service_code,code)
                aggiungi_unico(commento_code,following_service_code,code)
       
        if(health_service_code):
            if medical_examination==True:
                if follow_up==False:#1a Visita Specialistica
                   dizionario = recupera_amb_json_flow(1)
                   #print("ccc")
                   voce="optionals"
                   lungo=len(accessorie)
                   dizionario['main_exam']=name
                   dizionario['list_health_services'].extend(accessorie)
                   dizionario['list_health_servicesUUID'].extend(accessorie_uuid)
                   dizionario['health_service_code'].extend(accessorie_code)
                   dizionario['sector'].extend([voce] * lungo)
                   resp=json.dumps(dizionario, indent=4, ensure_ascii=False)
                   #return resp
                elif follow_up==True:#Visita di Controllo
                    dizionario = recupera_amb_json_flow(2)
                    sector=["health_services"]
                    dizionario['sector'].extend([sector])
                    dizionario['main_exam']=name
                    dizionario['list_health_services']=name
                    dizionario['list_health_servicesUUID']=uuid
                    dizionario['health_service_code']=health_service_code
                    dizionario['no']['list_health_services']=follow_up_health_services[0]['name']
                    dizionario['no']['list_health_servicesUUID']=follow_up_health_services[0]['uuid']
                    dizionario['no']['health_service_code']=follow_up_health_services[0]['health_service_code']
                    dizionario['no']['yes']['action']=f"cancella_carrello({name}),salva_carrello({follow_up_health_services[0]['name']},'health_services')"
                    dizionario['no']['sector'].extend([sector])
                    #print(follow_up_health_services[0]['name'])
                    resp=json.dumps(dizionario, indent=4, ensure_ascii=False)
                    #return resp
            elif medical_examination==False:
                if requires_prescription==True:#Esame Strumentale con prescrizione
                    #print("OK")
                    lungo=len(accessorie)
                    lungo_b=len(prima_visita)
                    voce="optionals"
                    voce_a="opinions"
                    voce_b="prescriptions"
                    sector_a=[]
                    sector_b=[]
                    sector_c=[]
                    dizionario = recupera_amb_json_flow(3)
                    dizionario['main_exam']=name
                    dizionario['list_health_services']=name
                    dizionario['sector']="health_services"
                    dizionario['yes']['list_health_services'].extend(accessorie)
                    dizionario['yes']['list_health_servicesUUID'].extend(accessorie_uuid)
                    dizionario['yes']['health_service_code'].extend(accessorie_code)
                    dizionario['yes']['sector'].extend([voce] * lungo)
                    dizionario['no']['yes']['list_health_services'].extend(prima_visita)
                    dizionario['no']['yes']['list_health_servicesUUID'].extend(prima_visita_uuid)
                    dizionario['no']['yes']['health_service_code'].extend(prima_visita_code)
                    dizionario['no']['yes']['sector'].extend([voce_b] * lungo_b)
                    #print(len(prima_visita))
                    
                    dizionario['yes']['no']['list_health_services'].extend(prima_visita)
                    dizionario['yes']['no']['list_health_servicesUUID'].extend(prima_visita_uuid)
                    dizionario['yes']['no']['health_service_code'].extend(prima_visita_code)
                    dizionario['yes']['no']['sector'].extend([voce_a] * lungo_b)
                    dizionario['yes']['yes']['list_health_services'].extend(prima_visita)
                    dizionario['yes']['yes']['list_health_servicesUUID'].extend(prima_visita_uuid)
                    dizionario['yes']['yes']['health_service_code'].extend(prima_visita_code)

                    dizionario['yes']['yes']['sector'].extend([voce_a] * lungo_b)
                    resp=json.dumps(dizionario, indent=4, ensure_ascii=False)
                elif requires_prescription==False:
                    if requires_preliminary_visit==True:#Prima visita obbligatoria
                        dizionario = recupera_amb_json_flow(4)
                        dizionario['main_exam']=name
                        
                        lungo=len(visita_specialista)
                        lungo_a=len(commento)
                        sector=["health_services","preliminary_visits"]
                        sector_b=["health_services"]
                        voce="health_services"
                        voce_a="preliminary_visits"
                        dizionario['list_health_services'].extend(visita_specialista)
                        dizionario['list_health_servicesUUID']=uuid
                        dizionario['health_service_code']=health_service_code
                        dizionario['sector'].extend([voce] * lungo)
                        dizionario['no']['list_health_services'].extend(visita_specialista)
                        dizionario['no']['list_health_servicesUUID'].extend(visita_specialista_uuid)
                        dizionario['no']['health_service_code'].extend(visita_specialista_code)
                        dizionario['no']['sector'].extend([voce_a] * lungo)
                        list_health_services=[]
                        list_health_services.append(name)
                        visita_specialistas = ''.join(visita_specialista)
                        list_health_services.append(visita_specialistas)
                        dizionario['no']['yes']['sector'].extend(sector)
                        dizionario['no']['yes']['list_health_services'].extend(list_health_services)
                        dizionario['no']['yes']['list_health_servicesUUID'].extend(list_health_services)
                        dizionario['no']['yes']['health_service_code'].extend(list_health_services)
                        dizionario['no']['no']['action']=f"cancella_carrello({name}),salva_carrello({visita_specialistas},'health_services')"
                        dizionario['no']['no']['sector']=sector_b
                        resp=json.dumps(dizionario, indent=4, ensure_ascii=False)
                        #return resp
                    elif requires_preliminary_visit==False:
                        lungo=len(accessorie)
                        lungo_a=len(commento)
                        voce="optionals"
                        voce_a="opinions"
                        dizionario = recupera_amb_json_flow(5)
                        dizionario['main_exam']=name
                        dizionario['list_health_services'].extend(accessorie)
                        dizionario['list_health_servicesUUID'].extend(accessorie_uuid)
                        dizionario['health_service_code'].extend(accessorie_code)
                        dizionario['sector'].extend([voce] * lungo)
                        dizionario['yes']['list_health_services'].extend(commento)
                        dizionario['yes']['list_health_servicesUUID'].extend(commento_uuid)
                        dizionario['yes']['health_service_code'].extend(commento_code)
                        dizionario['yes']['sector'].extend([voce_a] * lungo_a)
                        dizionario['yes']['no']['list_health_services'].extend(commento)
                        dizionario['yes']['no']['list_health_servicesUUID'].extend(commento_uuid)
                        dizionario['yes']['no']['health_service_code'].extend(commento_code)
                        dizionario['yes']['no']['sector'].extend([voce_a] * lungo_a)
                        dizionario['yes']['no']['yes']['action']=f"salva_carrello({name},{commento},'health_services','opinions')"
                        dizionario['yes']['no']['no']['action']=f"salva_carrello({name},'health_services')"
                        resp=json.dumps(dizionario, indent=4, ensure_ascii=False)
       
        return resp
    
##########Health Center############
# Tradate # c5535638-6c18-444c-955d-89139d8276be
################################################
##Visita Ortopedica Prima Visita##
# 1cc793b7-4a8b-4c54-ac09-3c7ca7e5a168
##################################
# RX Caviglia##
# 12826519-dd21-4e34-900e-ee8d471974a8
######################################
# ECG##
# 44896b8d-dee0-4b2e-8aa2-25f25606583e
#######################################
# Ecografia Addome Superiore
# 12826519-dd21-4e34-900e-ee8d471974a8
########################################
#risu=genera_flow("c48ff93f-1c88-4621-9cd5-31ad87e83e48","d9eb5830-3c36-4aa1-b0f5-20445ef2e825")
#print(risu)



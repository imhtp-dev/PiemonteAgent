def recupera_amb_json_flow(option):
    if option == 1:
        first_visit = {
            "main_exam": "",
            "message": "Hai bisogno anche di prenotare un servizio aggiuntivo?",
            "list_health_services": [],
            "list_health_servicesUUID": [],
            "health_service_code": [],
            "sector": [],
            "yes": {
                "message": "Salvataggio dei servizi richiesti nel carrello.",
                "action": "save_cart"
            },
            "no": {
                "message": "Salvataggio solo del servizio inizialmente richiesto nel carrello.",
                "action": "save_cart"
            }
        }
        return first_visit

    elif option == 2:
        follow_up_visit = {
            "main_exam": "",
            "message": "Il controllo è valido solo se eseguito entro 30 giorni dalla prima visita.\nHai fatto la prima visita entro 30 giorni?",
            "list_health_services": [],
            "list_health_servicesUUID": [],
            "health_service_code": [],
            "sector": [],
            "yes": {
                "message": "Saving the initially requested service in the cart.",
                "action": "save_cart"
            },
            "no": {
                "message": "The selected service requires a mandatory first visit.\nDo you want to book the first visit with a specialist?",
                "list_health_services": [],
                "list_health_servicesUUID": [],
                "health_service_code": [],
                "sector": [],
                "yes": {
                    "message": "Salvataggio dei servizi richiesti nel carrello.",
                    "action": "save_cart"
                },
                "no": {
                    "message": "The booking was unsuccessful.\nSorry, you cannot book the service without the first visit. Please contact the nearest Cerba center for more information.",
                }
            }
        }
        return follow_up_visit

    elif option == 3:
        instrumental_with_prescription = {
            "main_exam": "",
            "message": "This exam requires a prescription from a general practitioner or specialist.\nDo you have a medical prescription?",
            
            "yes": {
                "message": "Does your prescription include any of the following additional services you want to book?",
                "list_health_services": [],
                "list_health_servicesUUID": [],
                "health_service_code": [],
                "sector": [],
                "yes": {
                    "message": "Performing a diagnostic exam does not include the visit.\nDo you want to book a specialist visit to review the booked exams?",
                    "list_health_services": [],
                    "list_health_servicesUUID": [],
                    "health_service_code": [],
                    "sector": [],
                    "yes": {
                        "message": "Salvataggio dei servizi richiesti nel carrello.",
                        "action": "save_cart"
                    },
                    "no": {
                        "message": "Salvataggio solo del servizio inizialmente richiesto nel carrello.",
                        "action": "save_cart"
                    }
                },
                "no": {
                    "message": "Performing a diagnostic exam does not include the visit.\nDo you want to book a specialist visit to review the booked exams?",
                    "list_health_services": [],
                    "list_health_servicesUUID": [],
                    "health_service_code": [],
                    "sector": [],
                    "yes": {
                        "message": "Saving both requested services in the cart.",
                        "action": "save_cart"
                    },
                    "no": {
                        "message": "Salvataggio solo del servizio inizialmente richiesto nel carrello.",
                        "action": "save_cart"
                    }
                }
            },
            "no": {
                "message": "Do you want to book a visit with a doctor who can determine if a prescription is needed?",
                "yes": {
                    "message": "Specialist visit available:",
                    "list_health_services": [],
                    "list_health_servicesUUID": [],
                    "health_service_code": [],
                    "sector": [],
                    "yes": {
                        "message": "You chose the medical visit. Do you also want to keep the initial service?",
                        "yes": {
                            "message": "You chose to keep both services.",
                            "action": "save_cart"
                        },
                        "no": {
                            "message": "You chose not to keep the initial service, only the medical visit.",
                            "action": "save_cart"
                        }
                    }
                },
                "no": {
                    "message": "It is not possible to proceed without a medical prescription."
                }
            }
        }
        return instrumental_with_prescription

    elif option == 4:
        mandatory_first_visit = {
            "main_exam": "",
            "message": "Before starting treatment, the opinion of the performing doctor is required.\nHave you already had a visit with the specialist?",
            "list_health_services": [],
            "list_health_servicesUUID": [],
            "health_service_code": [],
            "sector": [],
            "yes": {
                "message": "Saving the initially requested service in the cart.",
                "action": "save_cart"
            },
            "no": {
                "message": "The selected service requires a mandatory visit. Do you want to book a visit with the specialist?",
                "list_health_services": [],
                "list_health_servicesUUID": [],
                "health_service_code": [],
                "sector": [],
                "yes": {
                    "message": "You chose to have a visit with a specialist. Do you also want to keep the initially selected service?",
                    "list_health_services": [],
                    "list_health_servicesUUID": [],
                    "health_service_code": [],
                    "sector": [],
                    "yes": {
                        "message": "Saving both services in the cart.",
                        "action": "save_cart"
                    },
                    "no": {
                        "message": "Removing the initial service and keeping the newly selected one.",
                        "sector": [],
                        "action": "save_cart"
                    }
                },
                "no": {
                    "message": "The booking was unsuccessful. Sorry, it is not possible to book the service without the visit. Please contact the nearest Cerba center for more information.",
                }
            }
        }
        return mandatory_first_visit

    elif option == 5:
        non_mandatory_first_visit = {
            "main_exam": "",
            "message": "Hai bisogno anche di prenotare un servizio aggiuntivo?",
            "list_health_services": [],
            "list_health_servicesUUID": [],
            "health_service_code": [],
            "sector": [],
            "yes": {
                "message": "Performing a diagnostic exam does not include the visit.\nDo you want to book a specialist visit to review the booked exams?",
                "list_health_services": [],
                "list_health_servicesUUID": [],
                "health_service_code": [],
                "sector": [],
                "yes": {
                    "message": "Saving the initial service, the additional service, and the visit in the cart",
                    "action": "save_cart"
                },
                "no": {
                    "message": "Performing a diagnostic exam does not include the visit.\nDo you want to book a specialist visit to review the booked exams?",
                    "list_health_services": [],
                    "list_health_servicesUUID": [],
                    "health_service_code": [],
                    "sector": [],
                    "yes": {
                        "message": "Saving the initial service and the visit in the cart.",
                        "action": "save_cart"
                    },
                    "no": {
                        "message": "Saving only the initial service in the cart.",
                        "action": "save_cart"
                    }
                }
            }
        }
        return non_mandatory_first_visit

    else:
        return None

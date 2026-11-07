"""
Scripts to Validate the JSON Files
"""
import json
import re
from datetime import datetime

valid_aircraft_types = ["73G", "321", "789", "76F"]

def validate_static_info_json(filename):
    valid = True
    with open(filename, "r", encoding="utf-8") as f:
        static_info = json.load(f)

    # Validate gates
    gates = static_info.get("gate_info", static_info.get("gates", {}))
    for gate_id, gate_info in gates.items():
        gate_type = gate_info.get("gate_type")
        if gate_type not in [0, 1, 2]:
            print(f"Invalid gate_type: {gate_type} for gate_id: {gate_id}")
            valid = False
        
        wingspan = gate_info.get("max_wingspan")
        if not isinstance(wingspan, (int, float)) or isinstance(wingspan, bool) or wingspan <= 0:
            print(f"Invalid max_wingspan: {wingspan} for gate_id: {gate_id}")
            valid = False
        
        distance = gate_info.get("dist")
        if not isinstance(distance, int) or isinstance(distance, bool) or distance < -1:
            print(f"Invalid dist: {distance} for gate_id: {gate_id}")
            valid = False
        
        jetbridge = gate_info.get("jetbridge")
        if jetbridge not in [0, 1, False, True]:
            print(f"Invalid jetbridge value: {jetbridge} for gate_id: {gate_id}")
            valid = False
    
    # Validate aircraft_info
    for aircraft_id, aircraft_info in static_info.get('aircraft_info', {}).items():
        wingspan = aircraft_info.get("wingspan")
        if not isinstance(wingspan, (int, float)) or isinstance(wingspan, bool) or wingspan <= 0:
            print(f"Invalid wingspan: {wingspan} for aircraft_id: {aircraft_id}")
            valid = False
        
        jetbridge_required = aircraft_info.get("jetbridge_required")
        if jetbridge_required not in [0, 1, False, True]:
            print(f"Invalid jetbridge_required value: {jetbridge_required} for aircraft_id: {aircraft_id}")
            valid = False

    if valid: 
        return 1
    if not valid: 
        print("ERROR :: Static JSON File Validation Failed.")
        return 0
    

def validate_flight_schedule_json(filename):
    valid = True
    with open(filename, "r", encoding="utf-8") as f:
        flight_schedule = json.load(f)

    for time, flightMessages in flight_schedule.items():
        if not isinstance(time, str):
            print(f"Invalid time format: {time}")
            valid = False
        
        for message in flightMessages:

            # -----------------------
            # Action validation
            # -----------------------
            if message['action'] not in [
                "CreateFlight", "ReplaceFlight", "UpdateTiming",
                "CancelFlight", "ReinstateFlight", "UpdateEquipment",
                "DivertFlight", "GateOutage"
            ]:
                print(f"Invalid message_type: {message['action']} at time: {time}")
                valid = False

            # Gate outages are resource messages, not flight messages. The
            # evaluator supports them directly and they have no flight object.
            if message.get("action") == "GateOutage":
                if not isinstance(message.get("gate"), str) or not message["gate"]:
                    print(f"Invalid gate at time: {time}")
                    valid = False
                for field in ("scheduledStartTime", "scheduledEndTime"):
                    value = message.get(field)
                    if value is not None and (not isinstance(value, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value)):
                        print(f"Invalid {field}: {value} at time: {time}")
                        valid = False
                continue

            # -----------------------
            # Flight object validation
            # -----------------------
            flight = message.get("flight")
            if not isinstance(flight, dict):
                print(f"Missing or invalid flight object at time: {time}")
                valid = False
                continue

            # Carrier: exactly 2 uppercase letters
            carrier = flight.get("carrier")
            if not isinstance(carrier, str) or not re.fullmatch(r"[A-Z]{2}", carrier):
                print(f"Invalid Carrier: {carrier} at time: {time}")
                valid = False

            # Flight Number: 1-4 digits
            flightNumber = flight.get("flightNumber")
            if not isinstance(flightNumber, str) or not re.fullmatch(r"\d{1,4}", flightNumber):
                print(f"Invalid Flight Number: {flightNumber} at time: {time}")
                valid = False

            # Flight Suffix: single letter or None
            flightSuffix = flight.get("flightSuffix")
            if flightSuffix is not None:
                if not isinstance(flightSuffix, str) or not re.fullmatch(r"[A-Z]", flightSuffix):
                    print(f"Invalid Flight Suffix: {flightSuffix} at time: {time}")
                    valid = False

            # Originating Date: ISO YYYY-MM-DD
            originatingDate = flight.get("originatingDate")
            try:
                datetime.strptime(originatingDate, "%Y-%m-%d")
            except:
                print(f"Invalid Originating Date: {originatingDate} at time: {time}")
                valid = False

            action = message['action']

            # Legs validation (Create, Replace, UpdateTiming)

            if action in ["CreateFlight", "ReplaceFlight", "UpdateTiming", "DivertFlight"]:
                legs = message.get("legs")
                if not isinstance(legs, list) or len(legs) == 0:
                    print(f"Missing or invalid legs array at time: {time}")
                    valid = False
                    continue

                for i, leg in enumerate(legs):
                    if not isinstance(leg, dict):
                        print(f"Invalid leg format at time: {time}")
                        valid = False
                        continue

                    # Leg Type validation
                    legType = leg.get("legType")
                    if legType not in ["Originating", "Through"]:
                        print(f"Invalid legType: {legType} at time: {time}")
                        valid = False

                    if i == 0 and legType != "Originating":
                        print(f"First leg must be Originating at time: {time}")
                        valid = False

                    if i > 0 and legType != "Through":
                        print(f"Subsequent legs must be Through at time: {time}")
                        valid = False

                    # Aircraft Type: exactly 3 characters
                    aircraftType = leg.get("aircraftType")
                    if not isinstance(aircraftType, str) or len(aircraftType) != 3 or aircraftType not in valid_aircraft_types:
                        print(f"Invalid aircraftType: {aircraftType} at time: {time}")
                        valid = False

                    # Station codes: exactly 3 uppercase letters
                    dep = leg.get("departureStation")
                    arr = leg.get("arrivalStation")
                    if not isinstance(dep, str) or not re.fullmatch(r"[A-Z]{3}", dep):
                        print(f"Invalid departureStation: {dep} at time: {time}")
                        valid = False
                    if not isinstance(arr, str) or not re.fullmatch(r"[A-Z]{3}", arr):
                        print(f"Invalid arrivalStation: {arr} at time: {time}")
                        valid = False

                    # Time format HH:MM
                    for tfield in ["scheduledDepartureTime", "scheduledArrivalTime"]:
                        tvalue = leg.get(tfield)
                        if not isinstance(tvalue, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", tvalue):
                            print(f"Invalid {tfield}: {tvalue} at time: {time}")
                            valid = False

                    # Leg connection rule
                    if i > 0:
                        prev_arrival = legs[i-1].get("arrivalStation")
                        if prev_arrival != dep:
                            print(f"Leg connection mismatch at time: {time}")
                            valid = False

            # CancelFlight validation
            if action == "CancelFlight":
                cancelScope = message.get("cancelScope")
                if not isinstance(cancelScope, dict):
                    print(f"Missing cancelScope at time: {time}")
                    valid = False
                else:
                    scopeType = cancelScope.get("type")
                    if scopeType not in ["AllLegs", "SpecificLeg"]:
                        print(f"Invalid cancelScope type: {scopeType} at time: {time}")
                        valid = False

                    if scopeType == "SpecificLeg":
                        dep = cancelScope.get("departureStation")
                        arr = cancelScope.get("arrivalStation")
                        if not re.fullmatch(r"[A-Z]{3}", str(dep)):
                            print(f"Invalid cancel departureStation at time: {time}")
                            valid = False
                        if not re.fullmatch(r"[A-Z]{3}", str(arr)):
                            print(f"Invalid cancel arrivalStation at time: {time}")
                            valid = False

            # UpdateEquipment 
            if action == "UpdateEquipment":
                equipmentUpdate = message.get("equipmentUpdate")
                if not isinstance(equipmentUpdate, dict):
                    print(f"Missing equipmentUpdate at time: {time}")
                    valid = False
                else:
                    scope = equipmentUpdate.get("scope")
                    if scope not in ["AllLegs", "SpecificLeg"]:
                        print(f"Invalid equipment scope: {scope} at time: {time}")
                        valid = False

                    aircraftType = equipmentUpdate.get("aircraftType")
                    if not isinstance(aircraftType, str) or len(aircraftType) != 3:
                        print(f"Invalid equipment aircraftType at time: {time}")
                        valid = False

                    if scope == "SpecificLeg":
                        dep = equipmentUpdate.get("departureStation")
                        arr = equipmentUpdate.get("arrivalStation")
                        if not re.fullmatch(r"[A-Z]{3}", str(dep)):
                            print(f"Invalid equipment departureStation at time: {time}")
                            valid = False
                        if not re.fullmatch(r"[A-Z]{3}", str(arr)):
                            print(f"Invalid equipment arrivalStation at time: {time}")
                            valid = False

    if valid: 
        return 1
    if not valid: 
        print("ERROR :: Flight Schedule JSON File Validation Failed.")
        return 0

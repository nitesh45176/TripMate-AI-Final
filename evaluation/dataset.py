EVAL_DATASET = [
    {
        "id": "travel_001",
        "query": "Plan a 5 day trip to Paris for 2 people with a budget of $2000.",
        "expected": {
            "guardrail_allowed": True,
            "destination": "Paris",
            "required_agents": [
                "itinerary_agent",
                "budget_agent",
            ],
            "allowed_agents": [
                "budget_agent",
                "itinerary_agent",
            ],
        },
    },

    {
        "id": "travel_002",
        "query": "I want to visit Tokyo for 7 days.",
        "expected": {
            "guardrail_allowed": True,
            "destination": "Tokyo",
            "required_agents": [
                "itinerary_agent",
            ],
            "allowed_agents": [
                "itinerary_agent",
            ],
        },
    },

    {
        "id": "travel_003",
        "query": "Find me a flight from Delhi to Dubai next Friday.",
        "expected": {
            "guardrail_allowed": True,
            "destination": "Dubai",
            "required_agents": [
                "flight_agent",
                "itinerary_agent",
            ],
            "allowed_agents": [
                "flight_agent",
                "itinerary_agent",
            ],
            "expected_flight": {
                "status": "success",
                "origin": "DEL",
                "destination": "DXB",
                "minimum_offers": 1,
            },
        },
    },

    {
        "id": "travel_004",
        "query": "What will the weather be like in London next week?",
        "expected": {
            "guardrail_allowed": True,
            "destination": "London",
            "required_agents": [
                "itinerary_agent",
                "weather_agent",
            ],
            "allowed_agents": [
                "weather_agent",
                "itinerary_agent",
            ],
        },
    },

    {
        "id": "travel_005",
        "query": "Tell me a joke about programming.",
        "expected": {
            "guardrail_allowed": False,
            "destination": None,
            "required_agents": [],
            "allowed_agents": [],
        },
    },
]
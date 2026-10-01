def ticket_request(model):
    return {
        "model": model,
        "state": {"ticket": "I was charged twice. Please refund the extra payment."},
        "questions": {
            "team": {
                "type": "choice",
                "instructions": "Which team should handle this ticket?",
                "criteria": {
                    "billing": "Payments and refunds",
                    "technical": "Bugs and integrations",
                    "other": "None of the above",
                },
            },
            "refund": {
                "type": "noul",
                "instructions": "Does the customer explicitly ask for a refund?",
            },
            "urgency": {
                "type": "score",
                "instructions": "How urgent is this ticket?",
                "criteria": ["Routine", "Soon", "Urgent"],
            },
        },
    }


def assert_ticket_response(response):
    assert response["model"]
    for name, question_type in (
        ("team", "choice"),
        ("refund", "noul"),
        ("urgency", "score"),
    ):
        assert response["answers"][name]["type"] == question_type
    assert isinstance(response["usage"]["input_tokens"], int)
    assert isinstance(response["usage"]["output_tokens"], int)

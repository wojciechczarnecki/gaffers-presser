from typing import get_args

from app.extraction.schemas import EventType
from app.retrieval.evaluation.queries import load_templates


def test_every_event_type_has_an_en_and_a_pl_template_with_the_player():
    templates = load_templates()
    for language in ("en", "pl"):
        assert set(templates[language]) == set(get_args(EventType))
        for template in templates[language].values():
            assert "{player}" in template

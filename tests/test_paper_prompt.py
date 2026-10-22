from pathlib import Path
import re

import pytest

from src.stage3.instantiate_cases import llm_instantiate


class RecordingClient:
    def __init__(self):
        self.messages = []

    def chat_json(self, *, system_prompt, user_prompt):
        self.messages.append((system_prompt, user_prompt))
        return {'setup_queries': ['CREATE (:Person {name: "Alice"})'],
                'main_query': 'MATCH (n:Person) RETURN n.name AS name',
                'cleanup_queries': [], 'notes': ''}


@pytest.mark.parametrize('arm', ['W', 'S'])
def test_ws_model_request_has_no_unrelated_generation_route(arm):
    root = Path(__file__).resolve().parents[1]
    prompt = (root/'prompts/instantiate_case_system.txt').read_text(encoding='utf-8')
    record = {'query_id': 'paper_prompt_test', 'source_id': 'source_report',
              'skeleton_id': 'sk1', 'skeleton_text': '',
              'guidance_skeleton_text': 'MATCH (n:HOLE_LABEL) RETURN n',
              'generation_strategy': 'feature-driven-soft',
              'feature_items': [{'feature_id': 'f1', 'description': 'Return a property alias'}],
              'fragment_groups': [], 'composition_scope': arm}
    client = RecordingClient()
    result = llm_instantiate(record, client, prompt)
    assert result['validation']['is_valid']
    assert result['generation_strategy'] == 'feature-driven-soft'
    assert len(client.messages) == 1
    system, user = client.messages[0]
    assert not re.search(r'feature-driven(?!-soft)|feature-fragment-only', system+'\n'+user)
    assert 'GENERATION STRATEGY: feature-driven-soft' in user
    assert 'You may change the structure if needed' in user
    assert 'MATCH (n:HOLE_LABEL) RETURN n' in user
    assert 'Return a property alias' in user


def test_skeleton_request_retains_its_structural_guidance():
    root = Path(__file__).resolve().parents[1]
    prompt = (root/'prompts/instantiate_case_system.txt').read_text(encoding='utf-8')
    client = RecordingClient()
    result = llm_instantiate({'query_id': 'strict', 'skeleton_text': 'MATCH (n) RETURN n',
                             'generation_strategy': 'skeleton', 'fragment_groups': []},
                            client, prompt)
    assert result['validation']['is_valid']
    assert 'Use the skeleton as the primary structural guide.' in client.messages[0][1]
    assert 'Ignore the skeleton' not in client.messages[0][0]

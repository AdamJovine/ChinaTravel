import sys
import os

sys.path.append("./../../../")
project_root_path = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

if project_root_path not in sys.path:
    sys.path.insert(0, project_root_path)

from chinatravel.agent.nesy_agent.llm_driven_rec import LLMDrivenAgent


class TPCAgent(LLMDrivenAgent):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def run(self, query, prob_idx=None, oralce_translation=False):
        return super().run(query, oralce_translation=oralce_translation)

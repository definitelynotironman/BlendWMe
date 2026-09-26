"""The 1.0.5 agent loop as a brain: any OpenAI-compatible endpoint, pay per token.

OFF by default. BlendWMe 2.0 runs on the user's subscriptions; this engine is
only for people who switch it on in the preferences, and it always has a hard
token budget. A local Ollama model works too, through Ollama's
OpenAI-compatible endpoint (http://localhost:11434/v1).

The agent writes to the panel itself (agent.py), so this adapter only turns the
end of its run into the standard done / error event.
"""

import threading

from ..state import DONE, ERROR, session
from .base import Engine


class ApiEngine(Engine):
    name = "api"
    label = "API"
    raw_messages = True        # the agent builds its own prompt, scene summary and user-edit notes

    def __init__(self, agent):
        super().__init__()
        self.agent = agent

    def is_running(self):
        return self.agent.is_running()

    def reset(self):
        super().reset()
        self.agent.messages = []

    def start(self, message, on_event, config):
        if not config.get("api_enabled"):
            on_event({"type": "error", "brain": self.name,
                      "text": "The pay-per-token API brain is switched off. Turn it on in the preferences first."})
            return
        if not config.get("api_key"):
            on_event({"type": "error", "brain": self.name, "text": "Add an API key first (Brains > API)."})
            return
        new_task = bool(config.get("new_task")) or not self.agent.messages
        self.agent.send(message, config, new_task=new_task, record_user=False)
        self.thread = threading.Thread(target=self._watch, args=(on_event,), daemon=True)
        self.thread.start()

    def _watch(self, on_event):
        worker = self.agent.thread
        if worker is not None:
            worker.join()
        if session.status == ERROR:
            on_event({"type": "error", "brain": self.name, "text": session.last_error or "The API brain stopped."})
        else:
            on_event({"type": "done", "brain": self.name, "text": "", "finished": session.status == DONE})

    def interrupt(self):
        self.agent.stop()

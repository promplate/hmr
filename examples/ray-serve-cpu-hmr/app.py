"""A real `sshleifer/tiny-gpt2` CPU deployment behind an OpenAI-compatible API.

One fixed replica, autoscaling off. The model is loaded once in `__init__` and every completion
runs a real `model.generate`, so the object identities the smoke reads back (`id(self.model)`,
`id(type(self.model))`, `id(next(self.model.parameters()))`) are the identities of the thing
actually doing the work. This file contains no HMR logic of its own: all reload decisions belong
to the installed `ray_serve_hmr` package, which is what the smoke is verifying.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import ray_serve_hmr
import torch
from fastapi import FastAPI
from pydantic import BaseModel
from ray import serve
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL_ID = os.getenv("HMR_MODEL_ID", "sshleifer/tiny-gpt2")

api = FastAPI()


class CompletionRequest(BaseModel):
    model: str = MODEL_ID
    prompt: str = ""
    max_tokens: int = 16
    temperature: float = 0.0


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = MODEL_ID
    messages: list[ChatMessage] = []
    max_tokens: int = 16
    temperature: float = 0.0


@serve.deployment(num_replicas=1, max_ongoing_requests=8, ray_actor_options={"num_cpus": 1})
@serve.ingress(api)
class TinyGPT2:
    def __init__(self) -> None:
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.model = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=torch.float32)
        self.model.eval()
        self.load_count = 1
        self.loaded_at = time.time()
        # Install HMR after the model exists, so a publication can never race the load.
        self.hmr = ray_serve_hmr.install()
        print(f"MODEL_LOADED pid={os.getpid()} model_id={id(self.model)} load_count={self.load_count}", flush=True)

    def _identity(self) -> dict[str, Any]:
        """The identities that separate "same model, new code" from "the model was rebuilt"."""
        first_param = next(self.model.parameters())
        return {
            "pid": os.getpid(),
            "model_object_id": id(self.model),
            "model_class_id": id(type(self.model)),
            "model_class": type(self.model).__name__,
            "first_parameter_id": id(first_param),
            "first_parameter_data_ptr": first_param.data_ptr(),
            "tokenizer_object_id": id(self.tokenizer),
            "instance_id": id(self),
            "load_count": self.load_count,
            "loaded_at": self.loaded_at,
        }

    def _generate(self, prompt: str, max_tokens: int) -> str:
        inputs = self.tokenizer(prompt or self.tokenizer.eos_token, return_tensors="pt")
        with torch.no_grad():
            output = self.model.generate(
                **inputs,
                max_new_tokens=max(1, max_tokens),
                do_sample=False,
                pad_token_id=self.tokenizer.pad_token_id,
            )
        generated = output[0][inputs["input_ids"].shape[-1] :]
        return self.tokenizer.decode(generated, skip_special_tokens=True)

    @api.get("/v1/models")
    async def models(self) -> dict[str, Any]:
        return {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "owned_by": "ray-serve-cpu-hmr"}]}

    @api.get("/identity")
    async def identity(self) -> dict[str, Any]:
        return self._identity()

    @api.get("/hmr/state")
    async def hmr_state(self) -> dict[str, Any]:
        return ray_serve_hmr.state()

    @api.post("/v1/completions")
    async def completions(self, request: CompletionRequest) -> dict[str, Any]:
        text = self._generate(request.prompt, request.max_tokens)
        return {
            "id": f"cmpl-{uuid.uuid4().hex}",
            "object": "text_completion",
            "created": int(time.time()),
            "model": request.model,
            "choices": [{"index": 0, "text": text, "finish_reason": "length", "logprobs": None}],
            "usage": {"prompt_tokens": 0, "completion_tokens": request.max_tokens, "total_tokens": request.max_tokens},
            "x_identity": self._identity(),
        }

    @api.post("/v1/chat/completions")
    async def chat_completions(self, request: ChatRequest) -> dict[str, Any]:
        prompt = "\n".join(f"{message.role}: {message.content}" for message in request.messages)
        text = self._generate(prompt, request.max_tokens)
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request.model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": request.max_tokens, "total_tokens": request.max_tokens},
            "x_identity": self._identity(),
        }


app = TinyGPT2.bind()

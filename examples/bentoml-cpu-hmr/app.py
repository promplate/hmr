"""A modern BentoML service serving tiny-gpt2 on CPU over an OpenAI-compatible route.

The API takes its request model positionally, so the HTTP body is the OpenAI payload itself rather
than being nested under a parameter name. That keeps every request on BentoML's own
`api_endpoint` -> `serde.parse_request` -> `JSONSerde.deserialize_model` path, which is where
`bentoml-hmr` republishes source.
"""

import os
import time
from pathlib import Path

import bentoml
import torch
from pydantic import BaseModel

MODEL_ID = "sshleifer/tiny-gpt2"


def probe(kind: str, **data: object) -> None:
    import json

    print("BENTOML_PROBE " + json.dumps({"kind": kind, "pid": os.getpid(), **data}), flush=True)


class Message(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    messages: list[Message]
    model: str = MODEL_ID
    max_tokens: int = 8


class Choice(BaseModel):
    index: int
    message: Message
    finish_reason: str


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletion(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: Usage


@bentoml.service(workers=1, traffic={"timeout": 120})
class TinyGPT2:
    def __init__(self) -> None:
        from transformers import AutoModelForCausalLM, AutoTokenizer

        probe("weight_load_start", model_id=MODEL_ID)
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
        self.model = AutoModelForCausalLM.from_pretrained(MODEL_ID)
        self.model.eval()
        probe("setup_done", **self.identity())

    def identity(self) -> dict[str, object]:
        """Object identities a reload must not disturb: same instance, class, weights, and storage."""
        parameter = next(self.model.parameters())
        return {
            "model_id": id(self.model),
            "model_class": type(self.model).__name__,
            "model_class_id": id(type(self.model)),
            "service_instance_id": id(self),
            "parameters": parameter.data_ptr(),
        }

    @bentoml.api(route="/v1/chat/completions")
    def chat_completions(self, payload: ChatCompletionRequest, /) -> ChatCompletion:
        prompt = "\n".join(message.content for message in payload.messages)
        probe("generate_start", prompt=prompt, **self.identity())
        if prompt == "HMR_HOLD":
            # An in-flight request, held until the smoke releases it, so publication must defer.
            release = Path(os.environ["BENTOML_PROBE_RESULTS"]) / "release"
            probe("hold_begin")
            while not release.exists():
                time.sleep(0.05)
            probe("hold_end")
        inputs = self.tokenizer(prompt, return_tensors="pt")
        with torch.no_grad():
            # `transformers` 5.x types `from_pretrained` as a base class whose `generate` its own stubs reject; the call is real at runtime.
            output = self.model.generate(**inputs, max_new_tokens=payload.max_tokens, do_sample=False, pad_token_id=self.tokenizer.eos_token_id)  # type: ignore
        text = self.tokenizer.decode(output[0], skip_special_tokens=True)
        prompt_tokens = int(inputs["input_ids"].shape[1])
        completion_tokens = int(output.shape[1]) - prompt_tokens
        probe("generate_done", completion_tokens=completion_tokens, **self.identity())
        return ChatCompletion(
            id=f"chatcmpl-{time.monotonic_ns():x}",
            created=int(time.time()),
            model=payload.model,
            choices=[Choice(index=0, message=Message(role="assistant", content=text), finish_reason="length")],
            usage=Usage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, total_tokens=prompt_tokens + completion_tokens),
        )

"""AI / technology themes tracked by XENOM (configuration, not logic).

Source: the product requirements ("Generative AI, multi-agent AI, embodied/physical AI, robotics, digital twins,
edge AI/IoT/sensor fusion, multimodal AI, emotion/intent understanding, trustworthy/safe/explainable AI,
hallucination/reliability, humanoids"; the checkpoint adds AI models / Hugging Face) plus the technology entities
already registered in the database (AI, machine learning, IoT, semiconductors, blockchain, cloud, cybersecurity).

A theme matches an article only when a pattern occurs in its stored title/summary/text AND a markup-free verbatim
evidence span exists. Patterns are deliberately literal: an article that does not use these words is not "AI".
A theme with no evidence in the stored corpus is reported as such, never padded with generic articles.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    key: str
    label: str
    pattern: str  # regex, matched case-insensitively with word boundaries added by the engine


TECH_THEMES: tuple[Theme, ...] = (
    Theme("generative_ai", "Generative AI", r"generative ai|genai|gen ai|generative models?|large language models?|chatgpt|gpt-?\d\w*"),
    Theme("multi_agent_ai", "Multi-agent AI", r"multi-?agent|agentic|ai agents?"),
    Theme("embodied_ai", "Embodied / physical AI", r"embodied (?:ai|intelligence)|physical ai"),
    Theme("robotics", "Robotics", r"robots?|robotics?|cobots?"),
    Theme("digital_twins", "Digital twins", r"digital twins?"),
    Theme("edge_iot", "Edge AI / IoT / sensor fusion", r"edge ai|edge computing|iot|internet of things|sensor fusion"),
    Theme("multimodal_ai", "Multimodal AI", r"multi-?modal"),
    Theme("emotion_intent", "Emotion / intent understanding",
          r"emotion(?:al)? (?:ai|recognition|detection)|intent (?:recognition|understanding|detection)|affective computing"),
    Theme("trustworthy_ai", "Trustworthy / safe / explainable AI",
          r"trustworthy ai|responsible ai|ai safety|explainable ai|explainability|ai governance"),
    Theme("hallucination", "Hallucination / reliability", r"hallucinat\w*|ai reliability"),
    Theme("humanoids", "Humanoids", r"humanoids?"),
    Theme("creative_ai", "Creative AI (art, music, design)",
          r"ai[- ](?:art|music|generated art|generated music)|generative art|ai composer|ai[- ]design tools?|text-to-image|text-to-music"),
    Theme("ai_models", "AI models & frameworks", r"hugging ?face|open-?source models?|foundation models?|llms?|transformer models?"),
    Theme("ai_ml_general", "AI / machine learning (general)",
          r"artificial intelligence|machine learning|deep learning|neural networks?|ai"),
    Theme("semiconductors", "Semiconductors", r"semiconductors?|chip ?makers?|fabs?|osat|wafers?"),
    Theme("blockchain", "Blockchain", r"blockchain|distributed ledger"),
    Theme("cloud_computing", "Cloud computing", r"cloud computing|cloud platform|public cloud"),
    Theme("cybersecurity", "Cybersecurity", r"cyber-?security|ransomware|data breach"),
)

"""Compatibility entry point for the unified LLM attribution/extraction call."""
from .llm_attribution import analyze_attribution, facts_from_analysis


def extract_facts_with_llm(meeting, definitions):
    return facts_from_analysis(meeting, analyze_attribution(meeting, definitions))

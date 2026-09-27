"""Conversation provider registry."""
import conversation_codex
import conversation_claude

PROVIDERS = {provider.NAME: provider for provider in (conversation_codex, conversation_claude)}

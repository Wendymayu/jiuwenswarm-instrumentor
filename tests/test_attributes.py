from jiuwenswarm_instrumentor import attributes as A


def test_gen_ai_constants():
    assert A.GEN_AI_SYSTEM == "gen_ai.system"
    assert A.GEN_AI_REQUEST_MODEL == "gen_ai.request.model"
    assert A.GEN_AI_USAGE_INPUT_TOKENS == "gen_ai.usage.input_tokens"
    assert A.GEN_AI_TOOL_NAME == "gen_ai.tool.name"


def test_jiuwenclaw_constants():
    assert A.JIUWENCLAW_SESSION_ID == "jiuwenclaw.session.id"
    assert A.JIUWENCLAW_CHANNEL_ID == "jiuwenclaw.channel.id"

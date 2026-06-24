import pytest
from jiuwenswarm_instrumentor.wrap import patch_method


class _Fake:
    async def invoke(self, x):
        return x + 1


async def test_patch_wraps_method():
    fake_cls = _Fake

    def factory(original):
        async def wrapped(self, x):
            return await original(self, x) * 10
        return wrapped

    applied = patch_method(fake_cls, "invoke", factory)
    assert applied is True
    assert await fake_cls().invoke(2) == 30  # (2+1)*10


async def test_patch_idempotent():
    fake_cls = _Fake

    def factory(original):
        async def wrapped(self, x):
            return await original(self, x)
        return wrapped

    patch_method(fake_cls, "invoke", factory)
    second = patch_method(fake_cls, "invoke", factory)
    assert second is False  # already wrapped, skipped


def test_patch_missing_method_returns_false():
    class Empty:
        pass
    assert patch_method(Empty, "nope", lambda o: o) is False

"""Unit tests for the 302.AI High-Availability Model Gateway."""

import urllib.error
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from formaai.config import Settings
from formaai.infrastructure.model_gateway import (
    MediaCallResult,
    ModelCallResult,
    ModelGateway,
    ModelGatewayError,
)


@pytest.fixture
def mock_settings():
    return Settings(
        app_env="test",
        mysql_database="test_db",
        mysql_user="test_user",
        mysql_password=SecretStr("test_pass"),
        redis_url=SecretStr("redis://localhost:6379/0"),
        jwt_access_secret=SecretStr("test_access_secret_32_bytes_min!"),
        jwt_refresh_secret=SecretStr("test_refresh_secret_32_bytes_min!"),
        ai302_api_key=SecretStr("sk-test-302ai-key"),
        ai302_base_url="https://api.302.ai/v1",
        ai302_llm_primary_model="deepseek-chat",
        ai302_llm_fallback_model="gpt-4o",
        ai302_image_primary_model="doubao-seedream-5-0-260128",
        ai302_image_fallback_model="gpt-image-1.5-t2i",
        ai302_video_primary_model="minimax-hailuo-02",
        ai302_video_fallback_model="official-kling-v3",
        ai302_timeout_seconds=5.0,
        ai302_max_retries=2,
    )


def test_primary_model_success(mock_settings):
    gateway = ModelGateway(mock_settings)

    mock_resp = {
        "choices": [{"message": {"content": "Hello from DeepSeek"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }

    with patch.object(gateway, "_execute_http_request", return_value=mock_resp) as mock_req:
        result = gateway.call_chat_completion(messages=[{"role": "user", "content": "hi"}])

        assert isinstance(result, ModelCallResult)
        assert result.content == "Hello from DeepSeek"
        assert result.model_used == "deepseek-chat"
        assert result.provider == "302.ai"
        assert result.total_tokens == 15
        assert result.failover_occurred is False
        mock_req.assert_called_once()
        assert mock_req.call_args[1]["payload"]["model"] == "deepseek-chat"


def test_retry_on_429_then_succeed(mock_settings):
    gateway = ModelGateway(mock_settings)

    err_429 = urllib.error.HTTPError(
        url="https://api.302.ai/v1/chat/completions",
        code=429,
        msg="Too Many Requests",
        hdrs=MagicMock(),
        fp=None,
    )

    success_resp = {
        "choices": [{"message": {"content": "Recovered after retry"}}],
        "usage": {"total_tokens": 20},
    }

    with patch.object(gateway, "_execute_http_request", side_effect=[err_429, success_resp]):
        with patch("time.sleep") as mock_sleep:
            result = gateway.call_chat_completion(messages=[{"role": "user", "content": "hi"}])
            assert result.content == "Recovered after retry"
            assert mock_sleep.called


def test_primary_failover_to_fallback(mock_settings):
    gateway = ModelGateway(mock_settings)

    # All primary calls fail with 500 error
    err_500 = urllib.error.HTTPError(
        url="https://api.302.ai/v1/chat/completions",
        code=500,
        msg="Internal Server Error",
        hdrs=MagicMock(),
        fp=None,
    )

    fallback_resp = {
        "choices": [{"message": {"content": "Response from GPT-4o Fallback"}}],
        "usage": {"total_tokens": 30},
    }

    # First 2 calls fail on primary, 3rd call succeeds on fallback
    with patch.object(gateway, "_execute_http_request", side_effect=[err_500, err_500, fallback_resp]) as mock_req:
        with patch("time.sleep"):
            result = gateway.call_chat_completion(messages=[{"role": "user", "content": "hi"}])

            assert result.content == "Response from GPT-4o Fallback"
            assert result.model_used == "gpt-4o"
            assert result.failover_occurred is True
            # Verify primary was tried first, then fallback
            assert mock_req.call_args_list[0][1]["payload"]["model"] == "deepseek-chat"
            assert mock_req.call_args_list[2][1]["payload"]["model"] == "gpt-4o"


def test_generate_script_proposals_json_parsing(mock_settings):
    gateway = ModelGateway(mock_settings)

    proposals_json = """[
        {"title": "方案 A：爆款反转", "badge": "高促单", "desc": "痛点反转", "hook": "开场钩子", "body": "展开", "cta": "购买"},
        {"title": "方案 B：真实验证", "badge": "高信任", "desc": "数据实测", "hook": "实测钩子", "body": "实测", "cta": "抢购"},
        {"title": "方案 C：生活共鸣", "badge": "高完播", "desc": "场景带入", "hook": "场景钩子", "body": "共鸣", "cta": "关注"}
    ]"""

    mock_resp = {
        "choices": [{"message": {"content": proposals_json}}],
        "usage": {"total_tokens": 100},
    }

    with patch.object(gateway, "_execute_http_request", return_value=mock_resp):
        proposals, meta = gateway.generate_script_proposals("防晒跑鞋", "职场白领", "commerce")

        assert len(proposals) == 3
        assert proposals[0]["title"] == "方案 A：爆款反转"
        assert proposals[0]["badge"] == "高促单"
        assert meta.model_used == "deepseek-chat"


def test_generate_script_proposals_fallback_on_invalid_json(mock_settings):
    gateway = ModelGateway(mock_settings)

    mock_resp = {
        "choices": [{"message": {"content": "This is non-json garbage text from model"}}],
        "usage": {"total_tokens": 50},
    }

    with patch.object(gateway, "_execute_http_request", return_value=mock_resp):
        proposals, meta = gateway.generate_script_proposals("防晒跑鞋", "职场白领", "commerce")

        # Must fall back gracefully to 3 baseline proposals without crashing
        assert len(proposals) == 3
        assert "方案 A" in proposals[0]["title"]
        assert meta.total_tokens == 50


# =========================================================================
# Image Generation Model (T2I/I2I) HA Tests
# =========================================================================


def test_image_model_primary_success(mock_settings):
    gateway = ModelGateway(mock_settings)

    mock_cli_resp = {
        "status": "completed",
        "taskid": "img_task_123",
        "result_url": "https://file.302.ai/imgs/test1.jpg",
    }

    with patch.object(gateway, "_execute_cli_command", return_value=mock_cli_resp) as mock_cli:
        res = gateway.generate_keyframe_image(prompt="test prompt", aspect_ratio="9:16", wait=True)

        assert isinstance(res, MediaCallResult)
        assert res.status == "completed"
        assert res.model_used == "doubao-seedream-5-0-260128"
        assert res.media_url == "https://file.302.ai/imgs/test1.jpg"
        assert res.failover_occurred is False
        mock_cli.assert_called_once()
        assert "--model" in mock_cli.call_args[0][0]
        assert "doubao-seedream-5-0-260128" in mock_cli.call_args[0][0]


def test_image_model_failover_to_fallback(mock_settings):
    gateway = ModelGateway(mock_settings)

    err = ModelGatewayError("Primary image model rejected")
    fb_resp = {
        "status": "completed",
        "taskid": "img_task_fb",
        "result_url": "https://file.302.ai/imgs/fallback.jpg",
    }

    # Primary attempts fail, fallback succeeds
    with patch.object(gateway, "_execute_cli_command", side_effect=[err, err, fb_resp]) as mock_cli:
        with patch("time.sleep"):
            res = gateway.generate_keyframe_image(prompt="test prompt", aspect_ratio="9:16", wait=True)

            assert res.status == "completed"
            assert res.model_used == "gpt-image-1.5-t2i"
            assert res.media_url == "https://file.302.ai/imgs/fallback.jpg"
            assert res.failover_occurred is True


def test_image_model_graceful_offline_fallback(mock_settings):
    gateway = ModelGateway(mock_settings)

    err = ModelGatewayError("Complete network disconnect")

    with patch.object(gateway, "_execute_cli_command", side_effect=err):
        with patch("time.sleep"):
            res = gateway.generate_keyframe_image(prompt="test prompt", aspect_ratio="9:16", wait=True)

            assert res.status == "completed"
            assert res.model_used == "deterministic_fallback"
            assert res.media_url is not None
            assert res.failover_occurred is True


# =========================================================================
# Video Generation Model (T2V/I2V) HA Tests
# =========================================================================


def test_video_model_primary_success(mock_settings):
    gateway = ModelGateway(mock_settings)

    mock_cli_resp = {
        "status": "pending",
        "taskid": "vid_task_456",
        "result_url": "https://file.302.ai/videos/test.mp4",
    }

    with patch.object(gateway, "_execute_cli_command", return_value=mock_cli_resp) as mock_cli:
        res = gateway.generate_shot_video_task(prompt="dynamic action scene", duration=5, aspect_ratio="9:16")

        assert isinstance(res, MediaCallResult)
        assert res.model_used == "minimax-hailuo-02"
        assert res.task_id == "vid_task_456"
        assert res.failover_occurred is False
        mock_cli.assert_called_once()
        assert "minimax-hailuo-02" in mock_cli.call_args[0][0]


def test_video_model_failover_to_fallback(mock_settings):
    gateway = ModelGateway(mock_settings)

    err = ModelGatewayError("MiniMax server busy 503")
    fb_resp = {
        "status": "pending",
        "taskid": "kling_task_789",
        "result_url": "https://file.302.ai/videos/kling.mp4",
    }

    with patch.object(gateway, "_execute_cli_command", side_effect=[err, err, fb_resp]):
        with patch("time.sleep"):
            res = gateway.generate_shot_video_task(prompt="dynamic action scene", duration=5, aspect_ratio="9:16")

            assert res.model_used == "official-kling-v3"
            assert res.task_id == "kling_task_789"
            assert res.failover_occurred is True


def test_video_model_graceful_offline_fallback(mock_settings):
    gateway = ModelGateway(mock_settings)

    err = ModelGatewayError("Video cluster unreachable")

    with patch.object(gateway, "_execute_cli_command", side_effect=err):
        with patch("time.sleep"):
            res = gateway.generate_shot_video_task(prompt="dynamic action scene", duration=5, aspect_ratio="9:16")

            assert res.model_used == "deterministic_fallback"
            assert res.task_id is not None
            assert res.failover_occurred is True


def test_generate_script_proposals_with_multimodal_vision(mock_settings):
    gateway = ModelGateway(mock_settings)

    mock_resp = {
        "choices": [
            {
                "message": {
                    "content": (
                        '['
                        '{"title":"方案 A：视觉质感篇","badge":"质感推荐","desc":"聚焦参考图设计细节","hook":"开篇展示实物微距材质","body":"解析精湛做工","cta":"立即下单"},'
                        '{"title":"方案 B：场景应用篇","badge":"场景推荐","desc":"高频户外场景实拍","hook":"户外雨水泥泞测试","body":"强悍抓地力解析","cta":"立即锁定"},'
                        '{"title":"方案 C：硬核拆解篇","badge":"硬核推荐","desc":"透气网孔显微特写","hook":"超轻克重实秤","body":"科技面料拆解","cta":"支持试穿"}'
                        ']'
                    )
                }
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 60, "total_tokens": 180},
    }

    with patch.object(gateway, "_execute_http_request", return_value=mock_resp) as mock_req:
        proposals, meta = gateway.generate_script_proposals(
            theme="轻量越野跑鞋",
            target_market="户外爱好者",
            content_type="commerce",
            image_urls=["data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="],
        )

        assert len(proposals) >= 1
        assert "视觉质感" in proposals[0]["title"]
        # Verify preferred model routed to vision model (gpt-4o) since deepseek is text only
        payload = mock_req.call_args[1]["payload"]
        assert payload["model"] == "gpt-4o"
        messages = payload["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert isinstance(user_msg["content"], list)
        assert any(item.get("type") == "image_url" for item in user_msg["content"])


def test_video_model_with_reference_image(mock_settings):
    gateway = ModelGateway(mock_settings)

    mock_cli_resp = {
        "status": "pending",
        "taskid": "vid_task_i2v_999",
        "result_url": "https://file.302.ai/videos/i2v.mp4",
    }

    with patch.object(gateway, "_execute_cli_command", return_value=mock_cli_resp) as mock_cli:
        res = gateway.generate_shot_video_task(
            prompt="镜头由静物平滑推向微距细节",
            duration=4,
            aspect_ratio="9:16",
            image_url="https://images.unsplash.com/photo-1542291026-7eec264c27ff",
        )

        assert res.model_used == "minimax-hailuo-02"
        mock_cli.assert_called_once()
        args = mock_cli.call_args[0][0]
        assert "--image" in args
        assert "https://images.unsplash.com/photo-1542291026-7eec264c27ff" in args



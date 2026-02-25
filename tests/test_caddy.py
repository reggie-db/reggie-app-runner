from reggie_app_runner import caddy
from reggie_app_runner.config import AppConfig


def test_build_config_sorts_longest_path_first():
    app_short = AppConfig(
        index=0,
        source="https://github.com/org/one",
        route_path="/app",
        strip_path_prefix=True,
        env={},
        command=["pixi", "run", "start"],
        port=4100,
        git_token=None,
        public_subdomain=None,
        tunnel_token=None,
        tunnel_remote="nectus.io",
        update_interval_seconds=30.0,
        redeploy_retry_seconds=5.0,
    )
    app_long = AppConfig(
        index=1,
        source="https://github.com/org/two",
        route_path="/app/internal",
        strip_path_prefix=False,
        env={},
        command=["pixi", "run", "start"],
        port=4200,
        git_token=None,
        public_subdomain=None,
        tunnel_token=None,
        tunnel_remote="nectus.io",
        update_interval_seconds=30.0,
        redeploy_retry_seconds=5.0,
    )

    payload = caddy.build_config(8000, [app_short, app_long])
    routes = payload["apps"]["http"]["servers"]["srv0"]["routes"]

    assert routes[0]["match"][0]["path"] == ["/app/internal*"]
    assert routes[1]["match"][0]["path"] == ["/app*"]


def test_build_config_adds_strip_prefix_handler_when_enabled():
    app_config = AppConfig(
        index=0,
        source="https://github.com/org/repo",
        route_path="/prefix",
        strip_path_prefix=True,
        env={},
        command=["pixi", "run", "start"],
        port=4300,
        git_token=None,
        public_subdomain=None,
        tunnel_token=None,
        tunnel_remote="nectus.io",
        update_interval_seconds=30.0,
        redeploy_retry_seconds=5.0,
    )

    payload = caddy.build_config(8000, [app_config])
    handlers = payload["apps"]["http"]["servers"]["srv0"]["routes"][0]["handle"]

    assert handlers[0]["handler"] == "rewrite"
    assert handlers[0]["strip_path_prefix"] == "/prefix"
    assert handlers[1]["handler"] == "reverse_proxy"


def test_build_config_adds_fallback_redirect_to_summary_path():
    fallback = caddy.ProxyRoute(path="/_app_runner", port=4400, strip_path_prefix=True)

    payload = caddy.build_config(8000, [], fallback_route=fallback)
    routes = payload["apps"]["http"]["servers"]["srv0"]["routes"]
    fallback_route = routes[-1]
    handlers = fallback_route["handle"]

    assert handlers[0]["handler"] == "static_response"
    assert handlers[0]["status_code"] == 302
    assert handlers[0]["headers"]["Location"] == ["/_app_runner/"]


def test_build_config_adds_fallback_proxy_when_summary_at_root():
    fallback = caddy.ProxyRoute(path="/", port=4500, strip_path_prefix=False)

    payload = caddy.build_config(8000, [], fallback_route=fallback)
    routes = payload["apps"]["http"]["servers"]["srv0"]["routes"]
    fallback_route = routes[-1]
    handlers = fallback_route["handle"]

    assert handlers[0]["handler"] == "reverse_proxy"
    assert handlers[0]["upstreams"][0]["dial"] == "127.0.0.1:4500"

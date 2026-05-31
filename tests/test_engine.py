import pytest
from ghost_bypass.engine.engine import _is_cf_html, _is_cf_response
from ghost_bypass.proxy.manager import MLProxyManager
from ghost_bypass.engine.site_learner import SiteLearner
from ghost_bypass.cloudflare.handler import CloudflareHandler

def test_cf_html_detection():
    # Title-level signals
    assert _is_cf_html('<title>Just a moment...</title>')
    assert _is_cf_html('<div>Verifying you are human</div>')
    
    # Body-level signals  
    assert _is_cf_html('__cf_bm cookie detected')
    
    # Binary guard
    assert not _is_cf_html(b'\x1b\x0f binary')
    assert not _is_cf_html('')
    assert not _is_cf_html(None)

def test_cf_response_detection():
    cf_200 = '<title>Just a moment</title><div>cf_clearance</div><div>challenge-platform</div>'
    normal_200 = '<p>Protected by Cloudflare Ray ID abc123</p>'
    
    # 200 status needs 3+ signals
    assert _is_cf_response(200, cf_200)
    assert not _is_cf_response(200, normal_200)
    
    # 403 status needs 1 signal
    assert _is_cf_response(403, '<title>Just a moment</title>')
    assert _is_cf_response(503, '<div>ddos-guard</div>')

def test_proxy_manager_ucb_score():
    gl = {'total': 100, 'successes': 80, 'avg_latency': 1.0, 'success_rate': 0.8}
    dom_few = {'total': 2, 'successes': 1, 'avg_latency': 1.5, 'cf_blocked': False}
    dom_many = {'total': 80, 'successes': 64, 'avg_latency': 1.2, 'cf_blocked': False}
    
    s_few = MLProxyManager._ucb_score(gl, dom_few, 500, 100)
    s_many = MLProxyManager._ucb_score(gl, dom_many, 500, 100)
    
    assert s_few > s_many, "Few domain trials should score higher than many (exploration)"

def test_site_learner_initialization():
    learner = SiteLearner(memory_path="/tmp/fake_memory.json")
    chain = learner.get_chain("example.com")
    assert isinstance(chain, list)
    assert len(chain) > 0
    assert chain[0] == "L0_requests_basic"

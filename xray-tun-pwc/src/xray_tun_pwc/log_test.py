from xray_tun_pwc.log import ERROR, INFO, WARN, line, relayed


def test_line_quotes_only_when_needed():
    assert line(INFO, "start") == "INFO start"
    assert line(INFO, "start", links=2, direct=True) == "INFO start links=2 direct=true"
    assert line(WARN, "check", failed="a: b, c") == 'WARN check failed="a: b, c"'
    assert line(WARN, "check", name="") == 'WARN check name=""'
    assert line(ERROR, "x", q='a"b') == 'ERROR x q="a\\"b"'


def test_relayed_prefixes_the_childs_event():
    assert relayed("xtc", "WARN link_rejected index=1\n") == "WARN xtc.link_rejected index=1"


def test_relayed_keeps_foreign_lines_whole():
    assert relayed("xtc", "panic: boom\n") == 'WARN xtc.output msg="panic: boom"'

"""Sanity tests for backend proxy_resolver against verified ground-truth fixtures."""

from app.services.lending.proxy_resolver import (
    PM_MAGIC_FACTORY,
    PM_MAGIC_IMPL,
    PM_SAFE_FACTORY,
    PM_SAFE_FALLBACK_HANDLER,
    PM_SAFE_MASTER_COPY,
    magic_proxy_of,
    safe_proxy_of,
    to_checksum_address,
)


def test_safe_proxy_external_wallet_eoa():
    eoa = "0xAA35E5045783e2f9CA553cAD9545BaF213998Ab1"
    expected = "0x7D9291e9a2bEA12779e7c2757AF05A4ef0121A2E"
    assert safe_proxy_of(eoa) == expected


def test_magic_proxy_magic_link_eoa():
    eoa = "0x917c7378f3F9aAfFa29e1A92c726ef9bfb6378D4"
    expected = "0xa048278D51D83b3640065BeBDe8B82C4DdbDbe26"
    assert magic_proxy_of(eoa) == expected


def test_derivation_case_insensitive():
    lower = "0xaa35e5045783e2f9ca553cad9545baf213998ab1"
    mixed = "0xAA35E5045783e2f9CA553cAD9545BaF213998Ab1"
    assert safe_proxy_of(lower) == safe_proxy_of(mixed)
    assert magic_proxy_of(lower) == magic_proxy_of(mixed)


def test_known_constants_are_addresses():
    for a in [
        PM_SAFE_FACTORY,
        PM_SAFE_MASTER_COPY,
        PM_SAFE_FALLBACK_HANDLER,
        PM_MAGIC_FACTORY,
        PM_MAGIC_IMPL,
    ]:
        assert a.startswith("0x")
        assert len(a) == 42


def test_eip55_checksum_examples():
    # canonical EIP-55 checksums from the spec
    cases = [
        "0x52908400098527886E0F7030069857D2E4169EE7",
        "0x8617E340B3D01FA5F11F306F4090FD50E238070D",
        "0xde709f2102306220921060314715629080e2fb77",
        "0x27b1fdb04752bbc536007a920d24acb045561c26",
        "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
        "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
        "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
    ]
    for canonical in cases:
        assert to_checksum_address(canonical.lower()) == canonical

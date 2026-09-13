"""Phase 1 预先验证：把 plan §3 的 4 个测试一次跑完。

参考：docs/kpax-vault-pusd-migration-plan.md

任意一个 step 失败就停手，不要进 Phase 2 写合约。

Usage:
  KEEPER_PRIVATE_KEY=0x... \
  KEEPER_PROXY_ADDRESS=0xa996... \
  POLYMARKET_RELAYER_API_KEY=019df5f1-... \
  python scripts/verify_pusd_pre_flight.py            # 跑全部 4 步
  python scripts/verify_pusd_pre_flight.py --step 3.2 # 只跑某一步

涉及真实链上交互（每笔 ~$0.5 USDC.e 等价的 dust，用 keeper proxy 现有 1 pUSD）。
所有 step 都先 staticcall / dry-run 探测，确认大概率成功才发交易。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from typing import Any

import httpx
from eth_abi import encode as abi_encode
from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import AsyncHTTPProvider, AsyncWeb3, Web3
from web3.middleware import ExtraDataToPOAMiddleware


# ---------- 链上常量 ----------

RPC = "https://polygon-bor-rpc.publicnode.com"
CHAIN_ID = 137

USDC_NATIVE = Web3.to_checksum_address("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")
USDC_E      = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
PUSD        = Web3.to_checksum_address("0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB")
ONRAMP      = Web3.to_checksum_address("0x93070a847efEf7F70739046A929D47a521F5B8ee")
OFFRAMP     = Web3.to_checksum_address("0x2957922Eb93258b93368531d39fAcCA3B4dC5854")
DEPOSIT_WALLET_FACTORY = Web3.to_checksum_address("0x00000000000Fb5C9ADea0298D729A0CB3823Cc07")

RELAYER_URL = os.environ.get(
    "POLYMARKET_RELAYER_URL", "https://relayer-v2.polymarket.com"
)


# ---------- 简化 ABI ----------

ERC20_ABI = [
    {"type": "function", "name": "balanceOf", "stateMutability": "view",
     "inputs": [{"name": "a", "type": "address"}],
     "outputs": [{"type": "uint256", "name": ""}]},
    {"type": "function", "name": "allowance", "stateMutability": "view",
     "inputs": [{"name": "o", "type": "address"}, {"name": "s", "type": "address"}],
     "outputs": [{"type": "uint256", "name": ""}]},
    {"type": "function", "name": "approve", "stateMutability": "nonpayable",
     "inputs": [{"name": "s", "type": "address"}, {"name": "a", "type": "uint256"}],
     "outputs": [{"type": "bool", "name": ""}]},
    {"type": "function", "name": "transfer", "stateMutability": "nonpayable",
     "inputs": [{"name": "to", "type": "address"}, {"name": "a", "type": "uint256"}],
     "outputs": [{"type": "bool", "name": ""}]},
    {"type": "function", "name": "decimals", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "uint8", "name": ""}]},
    {"type": "function", "name": "symbol", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "string", "name": ""}]},
]

ONRAMP_ABI = [{
    "type": "function", "name": "wrap", "stateMutability": "nonpayable",
    "inputs": [
        {"name": "_asset", "type": "address"},
        {"name": "_to", "type": "address"},
        {"name": "_amount", "type": "uint256"},
    ],
    "outputs": [],
}]

OFFRAMP_ABI = [{
    "type": "function", "name": "unwrap", "stateMutability": "nonpayable",
    "inputs": [
        {"name": "_asset", "type": "address"},
        {"name": "_to", "type": "address"},
        {"name": "_amount", "type": "uint256"},
    ],
    "outputs": [],
}]

WALLET_ABI = [{
    "type": "function", "name": "nonce", "stateMutability": "view",
    "inputs": [], "outputs": [{"type": "uint256", "name": ""}],
}]


# ---------- 工具 ----------

def _require_env(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        sys.exit(f"❌ 缺环境变量: {key}")
    return val


def _print_header(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def _color(s: str, code: str) -> str:
    return f"\033[{code}m{s}\033[0m"


def _ok(msg: str) -> None:
    print(_color("✅ PASS  ", "32") + msg)


def _fail(msg: str) -> None:
    print(_color("❌ FAIL  ", "31") + msg)


def _info(msg: str) -> None:
    print(_color("ℹ️  ", "34") + msg)


# ---------- step 3.1 ----------

async def step_3_1_onramp_asset_acceptance(w3: AsyncWeb3) -> dict:
    """3.1 — 探测 Onramp 接受 USDC.e 还是 Native USDC（或两者都接受）。

    策略：用一个临时地址做 `eth_call` 模拟 wrap 调用，amount=1 wei、to=自己。
    - revert 信息含 `transferFrom` / `allowance` / `balance` → asset 是被接受的，
      只是没钱/没批，**资产是合法的**
    - revert 信息含 `paused` / `not supported` / `asset` → asset 没被接受
    - 成功（不应该）→ 也算被接受
    """
    _print_header("Step 3.1 — Onramp 接受哪些 asset")
    onramp = w3.eth.contract(address=ONRAMP, abi=ONRAMP_ABI)
    probe_caller = "0x0000000000000000000000000000000000001111"

    # Onramp custom errors decoded from sourcify source:
    # - 0x49b8b3ac OnlyUnpaused()      → asset is PAUSED / not registered
    # - 0xc891add2 InvalidAsset()      → asset explicitly rejected
    # - 0x7939f424 TransferFromFailed()→ asset accepted, transfer failed downstream (OK)
    # - 0x13be252b InsufficientAllowance()→ asset accepted (OK)
    # - 0xf4d678b8 InsufficientBalance()  → asset accepted (OK)
    REJECTED_SELECTORS = {
        "0x49b8b3ac": "OnlyUnpaused (asset paused or not registered)",
        "0xc891add2": "InvalidAsset (explicitly rejected)",
    }
    ACCEPTED_SELECTORS = {
        "0x7939f424": "TransferFromFailed (asset OK, transfer fails for our probe)",
        "0x13be252b": "InsufficientAllowance (asset OK, no allowance)",
        "0xf4d678b8": "InsufficientBalance (asset OK, no balance)",
        "0x90b8ec18": "TransferFailed (asset OK)",
    }

    out = {}
    for label, addr in [("Native USDC", USDC_NATIVE), ("USDC.e", USDC_E)]:
        try:
            await onramp.functions.wrap(addr, probe_caller, 1).call(
                {"from": probe_caller}
            )
            out[label] = "ACCEPTED (call did not revert — surprising)"
            _ok(f"{label} ({addr}) — accepted (call succeeded)")
            continue
        except Exception as exc:
            msg = str(exc)
            # extract first 4-byte selector if present
            sel = None
            import re
            m = re.search(r"0x[0-9a-fA-F]{8}", msg)
            if m:
                sel = m.group(0).lower()
            if sel in REJECTED_SELECTORS:
                out[label] = f"REJECTED: {REJECTED_SELECTORS[sel]} ({sel})"
                _fail(f"{label} ({addr}) — NOT accepted: {REJECTED_SELECTORS[sel]}")
            elif sel in ACCEPTED_SELECTORS:
                out[label] = f"ACCEPTED ({ACCEPTED_SELECTORS[sel]})"
                _ok(f"{label} ({addr}) — accepted by Onramp ({ACCEPTED_SELECTORS[sel]})")
            else:
                out[label] = f"UNCLEAR selector={sel} msg={msg[:80]}"
                _info(f"{label} ({addr}) — unclear ({sel}): {msg[:120]}")
    return out


# ---------- step 3.2 ----------

async def step_3_2_pusd_to_usdc_via_relayer(
    w3: AsyncWeb3, *, keeper_pk: str, proxy: str, api_key: str,
    output_asset: str, output_label: str, unwrap_amount_e6: int,
) -> dict:
    """3.2 — Relayer 路径：keeper proxy 里的 pUSD → keeper EOA 收到 USDC。"""
    _print_header(f"Step 3.2 — pUSD → {output_label} via PM Relayer (live $)")
    keeper = Account.from_key(keeper_pk)
    pusd = w3.eth.contract(address=PUSD, abi=ERC20_ABI)
    output = w3.eth.contract(address=output_asset, abi=ERC20_ABI)
    wallet = w3.eth.contract(address=Web3.to_checksum_address(proxy), abi=WALLET_ABI)

    bal_pusd_before = await pusd.functions.balanceOf(proxy).call()
    bal_out_before = await output.functions.balanceOf(keeper.address).call()
    _info(f"pre:   proxy pUSD = {bal_pusd_before / 1e6:.6f}, "
          f"keeper {output_label} = {bal_out_before / 1e6:.6f}")

    if bal_pusd_before < unwrap_amount_e6:
        _fail(f"proxy 里 pUSD ({bal_pusd_before}) 不够 unwrap "
              f"({unwrap_amount_e6})")
        return {"status": "INSUFFICIENT_FUNDS"}

    nonce = await wallet.functions.nonce().call()
    deadline = int(time.time()) + 240

    # 2 个 call: approve(offramp, X) + offramp.unwrap(asset, keeper_eoa, X)
    approve_data = bytes.fromhex("095ea7b3") + abi_encode(
        ["address", "uint256"], [OFFRAMP, unwrap_amount_e6],
    )
    unwrap_data = bytes.fromhex(
        Web3.keccak(text="unwrap(address,address,uint256)").hex()[:8]
    ) + abi_encode(
        ["address", "address", "uint256"],
        [output_asset, keeper.address, unwrap_amount_e6],
    )
    calls = [
        {"target": PUSD, "value": 0, "data": approve_data},
        {"target": OFFRAMP, "value": 0, "data": unwrap_data},
    ]

    # EIP-712 sign
    sig_hex = _sign_batch(
        keeper_pk=keeper_pk, wallet_addr=proxy,
        nonce=nonce, deadline=deadline, calls=calls,
    )
    body = _build_wallet_request(
        from_addr=keeper.address, wallet_addr=proxy,
        nonce=nonce, deadline=deadline, calls=calls, signature=sig_hex,
    )
    _info(f"submitting Batch: 2 calls, wallet nonce={nonce}, deadline=+240s")

    tx_id, _ = await _relayer_submit(api_key, keeper.address, body)
    _info(f"relayer tx_id={tx_id}")
    receipt = await _relayer_poll(api_key, keeper.address, tx_id)
    _info(f"relayer state={receipt['state']}, hash={receipt.get('transactionHash')}")
    if receipt["state"] not in ("STATE_MINED", "STATE_CONFIRMED"):
        _fail(f"relayer state={receipt['state']} — see polygonscan")
        return {"status": "RELAYER_FAIL", "receipt": receipt}

    bal_pusd_after = await pusd.functions.balanceOf(proxy).call()
    bal_out_after = await output.functions.balanceOf(keeper.address).call()
    delta_pusd = bal_pusd_before - bal_pusd_after
    delta_out = bal_out_after - bal_out_before
    _info(f"post:  proxy pUSD = {bal_pusd_after / 1e6:.6f}, "
          f"keeper {output_label} = {bal_out_after / 1e6:.6f}")
    _info(f"delta: proxy pUSD = -{delta_pusd / 1e6:.6f}, "
          f"keeper {output_label} = +{delta_out / 1e6:.6f}")

    if delta_pusd == unwrap_amount_e6 and delta_out == unwrap_amount_e6:
        _ok("round-trip 成功：1:1 unwrap，金额完全匹配")
        return {"status": "OK", "tx_hash": receipt.get("transactionHash")}
    _fail(f"金额不匹配：期望 -/+{unwrap_amount_e6}，实际 -{delta_pusd}/+{delta_out}")
    return {"status": "BALANCE_MISMATCH"}


# ---------- step 3.3 ----------

async def step_3_3_usdc_to_pusd_via_onramp(
    w3: AsyncWeb3, *, keeper_pk: str, proxy: str,
    input_asset: str, input_label: str, wrap_amount_e6: int,
) -> dict:
    """3.3 — keeper EOA 直接调 Onramp.wrap，pUSD 落到 proxy。"""
    _print_header(f"Step 3.3 — {input_label} → pUSD via Onramp (direct EOA call)")
    keeper = Account.from_key(keeper_pk)
    inp = w3.eth.contract(address=input_asset, abi=ERC20_ABI)
    pusd = w3.eth.contract(address=PUSD, abi=ERC20_ABI)
    onramp = w3.eth.contract(address=ONRAMP, abi=ONRAMP_ABI)

    bal_in_before = await inp.functions.balanceOf(keeper.address).call()
    bal_pusd_before = await pusd.functions.balanceOf(proxy).call()
    _info(f"pre:   keeper {input_label} = {bal_in_before / 1e6:.6f}, "
          f"proxy pUSD = {bal_pusd_before / 1e6:.6f}")
    if bal_in_before < wrap_amount_e6:
        _fail(f"keeper EOA 里 {input_label} ({bal_in_before}) 不够 wrap "
              f"({wrap_amount_e6})。先跑 step 3.2 攒一些。")
        return {"status": "INSUFFICIENT_FUNDS"}

    # 1) approve onramp
    await _send_eoa_tx(
        w3, keeper_pk,
        inp.functions.approve(ONRAMP, wrap_amount_e6),
        label=f"{input_label}.approve(onramp, {wrap_amount_e6})",
    )
    # 2) onramp.wrap
    await _send_eoa_tx(
        w3, keeper_pk,
        onramp.functions.wrap(input_asset, Web3.to_checksum_address(proxy), wrap_amount_e6),
        label=f"onramp.wrap({input_label}, proxy, {wrap_amount_e6})",
    )

    bal_in_after = await inp.functions.balanceOf(keeper.address).call()
    bal_pusd_after = await pusd.functions.balanceOf(proxy).call()
    delta_in = bal_in_before - bal_in_after
    delta_pusd = bal_pusd_after - bal_pusd_before
    _info(f"post:  keeper {input_label} = {bal_in_after / 1e6:.6f}, "
          f"proxy pUSD = {bal_pusd_after / 1e6:.6f}")
    _info(f"delta: keeper {input_label} = -{delta_in / 1e6:.6f}, "
          f"proxy pUSD = +{delta_pusd / 1e6:.6f}")

    if delta_in == wrap_amount_e6 and delta_pusd == wrap_amount_e6:
        _ok("wrap 成功：USDC 1:1 换成 pUSD，落到 proxy")
        return {"status": "OK"}
    _fail(f"金额不匹配：期望 -/+{wrap_amount_e6}，实际 -{delta_in}/+{delta_pusd}")
    return {"status": "BALANCE_MISMATCH"}


# ---------- step 3.4 ----------

async def step_3_4_cross_signer_relayer_key(
    *, keeper_pk: str, api_key: str,
) -> dict:
    """3.4 — KPAX 一把 Relayer key 能否为不同 signer 转发？

    我们不实际部署一个新 wallet；只是把 RELAYER_API_KEY_ADDRESS 头从 keeper EOA
    换成一个随机地址，看 PM 的 /submit 怎么响应。

    - 401/403 + 关于 key/address 的错误 → key 紧绑 1 个 EOA，必须每用户一把
    - 200 OK 或 400 + 关于 wallet/signature 的错误 → key 与提交者解耦，KPAX 一把
      key 可以转发任意签名者
    """
    _print_header("Step 3.4 — Relayer key 是否可跨 signer 转发")
    keeper = Account.from_key(keeper_pk)
    random_addr = Account.create().address

    # 构造一个 dust call（pUSD.balanceOf(self) 是 view，不用；改 transfer(0)）
    transfer_zero = bytes.fromhex("a9059cbb") + abi_encode(
        ["address", "uint256"], [keeper.address, 0],
    )
    # 这个 batch 反正会 revert（wallet 校验签名失败），我们看的是 PM 关怎么响应
    body = {
        "type": "WALLET",
        "from": keeper.address,
        "to": DEPOSIT_WALLET_FACTORY,
        "nonce": "0",
        "signature": "0x" + "00" * 65,
        "depositWalletParams": {
            "depositWallet": keeper.address,  # 占位
            "deadline": str(int(time.time()) + 60),
            "calls": [
                {"target": PUSD, "value": "0", "data": "0x" + transfer_zero.hex()},
            ],
        },
    }

    async with httpx.AsyncClient(timeout=15) as client:
        # 控制实验：用 keeper EOA 作 RELAYER_API_KEY_ADDRESS（默认情况）
        r1 = await client.post(
            RELAYER_URL.rstrip("/") + "/submit",
            json=body,
            headers={
                "RELAYER_API_KEY": api_key,
                "RELAYER_API_KEY_ADDRESS": keeper.address,
                "Content-Type": "application/json",
            },
        )
        # 测试：把 RELAYER_API_KEY_ADDRESS 换成一个随机地址
        r2 = await client.post(
            RELAYER_URL.rstrip("/") + "/submit",
            json=body,
            headers={
                "RELAYER_API_KEY": api_key,
                "RELAYER_API_KEY_ADDRESS": random_addr,
                "Content-Type": "application/json",
            },
        )

    _info(f"control (header={keeper.address[:8]}…):")
    _info(f"  status={r1.status_code}, body={r1.text[:200]}")
    _info(f"experiment (header={random_addr[:8]}…):")
    _info(f"  status={r2.status_code}, body={r2.text[:200]}")
    print()

    # 解读
    text2 = r2.text.lower()
    if r2.status_code in (401, 403) and any(
        kw in text2 for kw in ("key", "api", "auth", "address", "unauthorized")
    ):
        _ok("结论：key 紧绑 1 个 EOA。**每个借款人需自己创建一把 key**。")
        return {"status": "BOUND", "code": r2.status_code}
    elif r2.status_code in (200, 400, 422):
        _ok(
            "结论：key 与 RELAYER_API_KEY_ADDRESS 解耦。**KPAX 一把 key 可以"
            "为任意签名者转发**（前提是签名 + wallet ownership 是合法的）。"
        )
        return {"status": "DECOUPLED", "code": r2.status_code}
    else:
        _info(f"结论不明确：status={r2.status_code} text={r2.text[:120]}")
        return {"status": "UNCLEAR", "code": r2.status_code}


# ---------- relayer helpers ----------

async def _relayer_submit(api_key: str, signer: str, body: dict) -> tuple[str, dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            RELAYER_URL.rstrip("/") + "/submit",
            json=body,
            headers={
                "RELAYER_API_KEY": api_key,
                "RELAYER_API_KEY_ADDRESS": signer,
                "Content-Type": "application/json",
            },
        )
    if r.status_code != 200:
        raise RuntimeError(
            f"relayer /submit returned {r.status_code}: {r.text[:300]}"
        )
    data = r.json()
    return data.get("transactionID") or data.get("transactionId"), data


async def _relayer_poll(api_key: str, signer: str, tx_id: str, *, timeout_s: int = 120) -> dict:
    deadline = time.time() + timeout_s
    last_state = None
    async with httpx.AsyncClient(timeout=15) as client:
        while True:
            r = await client.get(
                RELAYER_URL.rstrip("/") + "/transaction",
                params={"id": tx_id},
                headers={
                    "RELAYER_API_KEY": api_key,
                    "RELAYER_API_KEY_ADDRESS": signer,
                },
            )
            if r.status_code != 200:
                raise RuntimeError(
                    f"relayer /transaction returned {r.status_code}: {r.text[:300]}"
                )
            data: Any = r.json()
            txn = data[0] if isinstance(data, list) and data else data
            if not isinstance(txn, dict):
                raise RuntimeError(f"unexpected response: {data}")
            state = txn.get("state")
            if state != last_state:
                _info(f"poll: state={state}")
                last_state = state
            if state in ("STATE_MINED", "STATE_CONFIRMED"):
                return txn
            if state in ("STATE_FAILED", "STATE_INVALID"):
                return txn
            if time.time() >= deadline:
                raise RuntimeError(f"poll timeout: tx_id={tx_id}, last_state={state}")
            await asyncio.sleep(2)


# ---------- EIP-712 ----------

def _sign_batch(
    *, keeper_pk: str, wallet_addr: str, nonce: int,
    deadline: int, calls: list[dict],
) -> str:
    domain = {
        "name": "DepositWallet",
        "version": "1",
        "chainId": CHAIN_ID,
        "verifyingContract": Web3.to_checksum_address(wallet_addr),
    }
    types = {
        "Call": [
            {"name": "target", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
        ],
        "Batch": [
            {"name": "wallet", "type": "address"},
            {"name": "nonce", "type": "uint256"},
            {"name": "deadline", "type": "uint256"},
            {"name": "calls", "type": "Call[]"},
        ],
    }
    message = {
        "wallet": Web3.to_checksum_address(wallet_addr),
        "nonce": nonce,
        "deadline": deadline,
        "calls": [
            {"target": Web3.to_checksum_address(c["target"]),
             "value": c["value"], "data": c["data"]}
            for c in calls
        ],
    }
    signable = encode_typed_data(full_message={
        "types": types, "domain": domain,
        "primaryType": "Batch", "message": message,
    })
    signed = Account.from_key(keeper_pk).sign_message(signable)
    return "0x" + signed.signature.hex()


def _build_wallet_request(
    *, from_addr: str, wallet_addr: str,
    nonce: int, deadline: int, calls: list[dict], signature: str,
) -> dict:
    return {
        "type": "WALLET",
        "from": from_addr,
        "to": DEPOSIT_WALLET_FACTORY,
        "nonce": str(nonce),
        "signature": signature,
        "depositWalletParams": {
            "depositWallet": Web3.to_checksum_address(wallet_addr),
            "deadline": str(deadline),
            "calls": [
                {"target": c["target"],
                 "value": str(c["value"]),
                 "data": "0x" + c["data"].hex()}
                for c in calls
            ],
        },
    }


# ---------- direct EOA tx helper ----------

async def _send_eoa_tx(w3: AsyncWeb3, pk: str, fn_call: Any, *, label: str) -> str:
    eoa = Account.from_key(pk)
    latest = await w3.eth.get_block("latest")
    base_fee = latest.get("baseFeePerGas") or 0
    tip = 30 * 10**9
    nonce = await w3.eth.get_transaction_count(eoa.address)
    tx = await fn_call.build_transaction({
        "from": eoa.address,
        "nonce": nonce,
        "chainId": CHAIN_ID,
        "maxPriorityFeePerGas": tip,
        "maxFeePerGas": base_fee * 2 + tip,
    })
    signed = eoa.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    sent = await w3.eth.send_raw_transaction(raw)
    h = sent.hex() if isinstance(sent, bytes) else str(sent)
    _info(f"sent: {label} → 0x{h}")
    deadline = time.time() + 90
    while True:
        try:
            r = await w3.eth.get_transaction_receipt(h)
        except Exception:
            r = None
        if r is not None:
            ok = r.get("status") in (1, "0x1")
            if not ok:
                raise RuntimeError(f"{label} reverted")
            _info(f"  confirmed in block {r.get('blockNumber')}")
            return h
        if time.time() >= deadline:
            raise RuntimeError(f"{label} never confirmed")
        await asyncio.sleep(2)


# ---------- main ----------

async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", choices=["3.1", "3.2", "3.3", "3.4", "all"], default="all")
    parser.add_argument(
        "--lp-asset", choices=["usdc.e", "native"], default="usdc.e",
        help="LP underlying decision (per plan §9 #1, default USDC.e)",
    )
    parser.add_argument(
        "--unwrap-amount", type=float, default=0.5,
        help="Step 3.2 unwrap 多少 pUSD（默认 0.5）",
    )
    parser.add_argument(
        "--wrap-amount", type=float, default=0.3,
        help="Step 3.3 wrap 多少 USDC（默认 0.3）",
    )
    args = parser.parse_args()

    keeper_pk = _require_env("KEEPER_PRIVATE_KEY")
    proxy = _require_env("KEEPER_PROXY_ADDRESS")
    api_key = _require_env("POLYMARKET_RELAYER_API_KEY")
    if keeper_pk.startswith("0x"):
        keeper_pk = keeper_pk[2:]

    w3 = AsyncWeb3(AsyncHTTPProvider(RPC))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    output_asset = USDC_E if args.lp_asset == "usdc.e" else USDC_NATIVE
    output_label = "USDC.e" if args.lp_asset == "usdc.e" else "Native USDC"

    results: dict = {}
    if args.step in ("3.1", "all"):
        results["3.1"] = await step_3_1_onramp_asset_acceptance(w3)
    if args.step in ("3.2", "all"):
        results["3.2"] = await step_3_2_pusd_to_usdc_via_relayer(
            w3, keeper_pk=keeper_pk, proxy=proxy, api_key=api_key,
            output_asset=output_asset, output_label=output_label,
            unwrap_amount_e6=int(args.unwrap_amount * 1e6),
        )
    if args.step in ("3.3", "all"):
        results["3.3"] = await step_3_3_usdc_to_pusd_via_onramp(
            w3, keeper_pk=keeper_pk, proxy=proxy,
            input_asset=output_asset, input_label=output_label,
            wrap_amount_e6=int(args.wrap_amount * 1e6),
        )
    if args.step in ("3.4", "all"):
        results["3.4"] = await step_3_4_cross_signer_relayer_key(
            keeper_pk=keeper_pk, api_key=api_key,
        )

    _print_header("汇总")
    for k, v in results.items():
        print(f"  step {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

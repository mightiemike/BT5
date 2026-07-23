### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Per-User Swap Allowlist via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual user. If the pool admin allowlists the router (required for any router-mediated swap to succeed on a curated pool), every unpermissioned user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
  msg.sender,   // <-- always the immediate caller of pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

So the extension receives `sender = address(router)`, not the actual user's address. The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same applies to `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` directly, making the pool see the router as `msg.sender`.

A pool admin who wants to support router-mediated swaps on a curated pool must allowlist the router. Once the router is allowlisted, **any** user — including those explicitly excluded from the allowlist — can swap by routing through `MetricOmmSimpleRouter`.

Note: `DepositAllowlistExtension` does **not** share this flaw because it gates by `owner` (the position owner, passed correctly through the liquidity adder), not by `sender`.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or institutional LPs) loses that restriction entirely for any user who routes through the public `MetricOmmSimpleRouter`. The disallowed user receives pool output tokens at oracle-anchored prices, draining LP value and breaking the pool's curation invariant. This is a direct loss of LP assets and a broken core pool functionality.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, documented periphery swap entry point. Any pool admin who deploys a curated pool and also wants to support normal router-based UX will allowlist the router — a natural and expected configuration. The bypass requires no special privileges, no flash loans, and no unusual token behavior: any EOA can call `exactInputSingle` on the router.

---

### Recommendation

The extension must gate on the **economically relevant actor** — the user who initiated the swap — not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the original initiator through the router**: Have `MetricOmmSimpleRouter` encode `msg.sender` (the actual user) into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and verify it. This requires a trusted router identity check inside the extension.

2. **Check `recipient` instead of `sender`**: If the pool's design guarantees that `recipient` is always the actual beneficiary, gate on `recipient`. However, this may not hold for multi-hop paths where intermediate recipients are the router itself.

3. **Separate router-level allowlist from pool-level allowlist**: Deploy a router wrapper that enforces the user-level allowlist before forwarding to the pool, and configure the pool to only accept calls from that wrapper.

The simplest safe fix is option 1: the extension decodes a signed or trusted user address from `extensionData` when `sender` is a known router, falling back to `sender` for direct calls.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps (necessary for normal UX).
3. Pool admin calls `setAllowedToSwap(pool, alice, false)` (or simply never allowlists Alice).
4. Alice calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(alice_recipient, ...)` — pool sees `msg.sender = router`.
6. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` checks `allowedSwapper[pool][router]` → `true` → passes.
7. Alice's swap executes and she receives output tokens, bypassing the per-user allowlist entirely.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```

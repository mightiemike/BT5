### Title
`SwapAllowlistExtension` gates the router address instead of the real user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument it receives from the pool, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that value is the **router address**, not the actual end user. A pool admin who allowlists the router to let legitimate users trade through it simultaneously opens the gate to every unprivileged caller on the network.

---

### Finding Description

**Call chain that exposes the bug**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         └─ pool.swap(recipient, ...) [msg.sender = router]
               └─ _beforeSwap(sender = msg.sender = router, ...)
                     └─ SwapAllowlistExtension.beforeSwap(sender = router, ...)
                           └─ allowedSwapper[pool][router]  ← checked, NOT the real user
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol line 162-165
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then gates on that forwarded `sender`:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct — prevents spoofing the pool key). `sender` is the router. The check therefore resolves to `allowedSwapper[pool][router]`.

**The bypass**

A pool admin who wants allowlisted users to be able to trade through the public router must add the router to the allowlist. Once the router is allowlisted, the guard is satisfied for **every** caller of `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput`, regardless of whether that caller is individually allowlisted. The router is a permissionless public contract; anyone can call it.

The symmetric failure also exists: if the admin does *not* allowlist the router, individually allowlisted users who route through the router are blocked, breaking the core swap flow for legitimate participants.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd market makers, whitelisted institutions, or protocol-controlled addresses) loses that restriction entirely for any caller who routes through `MetricOmmSimpleRouter`. The attacker:

1. Calls `router.exactInputSingle(pool, ...)` — no special privilege required.
2. The pool's `beforeSwap` hook sees `sender = router`, which is allowlisted.
3. The swap executes at the oracle-derived bid/ask price, transferring pool liquidity to the attacker.

Because the pool is oracle-priced, the attacker receives fair-value tokens from the pool's reserves. LP holders suffer direct loss of principal proportional to the volume the attacker trades. The pool admin's access-control intent is completely nullified.

---

### Likelihood Explanation

The precondition is that the pool admin allowlists the router — a natural and expected operational step for any pool that wants its legitimate users to trade through the standard periphery. The bypass requires no privileged access, no special token, and no complex setup: a single call to a public router function is sufficient. Any user who discovers the allowlisted router can exploit it immediately and repeatedly.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **real end user**, not the intermediary. Two sound approaches:

1. **Check `recipient` instead of (or in addition to) `sender`** — for single-hop swaps the recipient is the actual beneficiary; however this breaks for multi-hop paths where the router is the intermediate recipient.

2. **Require the real user identity in `extensionData`** — the router already forwards `extensionData` unchanged to the pool. The extension can require a signed or ABI-encoded user address in that field and verify it against the allowlist. The router would need to inject `msg.sender` into the extension payload before forwarding.

3. **Allowlist at the router level** — add a separate allowlist inside `MetricOmmSimpleRouter` that gates `exactInput*` / `exactOutput*` by `msg.sender` before calling the pool, so the pool-level extension never needs to reason about intermediaries.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls swapExtension.setAllowedToSwap(pool, address(router), true)
    (to let allowlisted users trade via the router)
  alice is NOT individually allowlisted

Attack:
  alice calls router.exactInputSingle({pool: pool, tokenIn: token0, ...})
  → router calls pool.swap(recipient=alice, ...)  [msg.sender = router]
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  → swap executes; alice receives pool tokens
  → alice repeats until pool liquidity is drained
```

**Corrupted value**: `allowedSwapper[pool][router]` is `true`, but the extension treats this as authorization for every caller of the router, not just the router itself. The identity actually checked diverges from the identity the pool admin intended to gate. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any unprivileged swapper to bypass a curated pool's allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. A pool admin who allowlists the router (required for any allowlisted user to trade through the router) simultaneously opens the gate to every unprivileged user who routes through the same public contract.

---

### Finding Description

**`SwapAllowlistExtension.beforeSwap` identity check:**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (enforced by `onlyPool`). `sender` is the value the pool passes, which is always `msg.sender` of `MetricOmmPool.swap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-L240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-L80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
```

The pool receives `msg.sender = router`. It passes `router` as `sender` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

**The trap for pool admins:** To let any allowlisted user trade through the router, the admin must call `setAllowedToSwap(pool, router, true)` or `setAllowAllSwappers(pool, true)`. Either action opens the gate to every user who calls the router, including those the allowlist was meant to exclude.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, protocol-owned accounts, or specific market makers) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The disallowed user executes real swaps against the pool's LP reserves at oracle-derived prices, extracting value from LPs who deposited under the assumption that only approved counterparties could trade. This is a direct loss of LP principal through unauthorized swap execution.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint documented for end-users. Any pool admin who deploys a curated pool and then allowlists the router (a natural operational step) immediately exposes the pool to all users. The attacker needs no special privileges, no flash loan, and no multi-step setup — a single call to `exactInputSingle` suffices.

---

### Recommendation

The extension must check the economically relevant actor — the end-user — not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the original user through the router:** The router stores the original `msg.sender` in transient storage (already done for the payer in `_setNextCallbackContext`). Extend this to pass the originating user as `callbackData` or a dedicated transient slot, and have the pool forward it as a separate `originator` field to extensions.

2. **Check `recipient` instead of `sender` for swap allowlists:** If the pool's design intent is to gate who receives output tokens, `recipient` is the correct field. However, for input-side gating, the originating user must be explicitly threaded through.

The simplest safe fix is to not allowlist the router address in `SwapAllowlistExtension` and instead require users to call `pool.swap()` directly when the pool is curated — but this must be documented explicitly, as the current design gives no warning.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists only address(allowedUser) via setAllowedToSwap(pool, allowedUser, true)
  - Pool admin also allowlists the router: setAllowedToSwap(pool, router, true)
    (required so allowedUser can trade through the router)

Attack:
  - address(attacker) is NOT in the allowlist
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap() → msg.sender in pool = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes; attacker receives output tokens

Result:
  - attacker bypassed the allowlist entirely
  - LP funds were traded against an unauthorized counterparty
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
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

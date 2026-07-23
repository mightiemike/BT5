### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Real Swapper, Allowing Any User to Bypass the Allowlist - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `sender`. If the pool admin allowlists the router address to let legitimate users trade via the router, every unprivileged user can bypass the allowlist by routing through the same public contract.

---

### Finding Description

The pool's `swap()` function passes `msg.sender` (its own direct caller) as the `sender` argument to every extension hook:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that argument against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` inside the extension is the pool (enforced by `onlyPool`). `sender` is whoever called `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...,
    params.extensionData
);
```

So when a user goes through the router, the pool sees `msg.sender = router`, and the extension receives `sender = router`. The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin faces an impossible choice:

| Router allowlisted? | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| No | Blocked (bad UX) | Blocked |
| Yes | Allowed | **Also allowed — bypass** |

Allowlisting the router to enable legitimate users to trade through it simultaneously opens the gate to every non-allowlisted address.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-internal actors) can be fully bypassed by any unprivileged user who calls `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`. The non-allowlisted user executes a real swap against pool liquidity, receiving output tokens and paying input tokens through the router callback — a direct policy bypass with fund-impacting consequences for LP positions on curated pools.

---

### Likelihood Explanation

The bypass requires no special privileges, no flash loan, and no multi-transaction setup. `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call it. The only precondition is that the pool admin has allowlisted the router address (a necessary step for any legitimate user to trade via the router), which is the normal operational configuration for a curated pool that still wants to support the standard periphery.

---

### Recommendation

The extension must gate on the **original end-user**, not on the direct caller of `pool.swap()`. Two complementary fixes:

1. **Pass the originating user through the router.** The router already knows `msg.sender` at entry. It could encode the original caller in `extensionData` and the extension could decode and verify it — but this is forgeable by any caller who constructs raw `extensionData`.

2. **Preferred: check `recipient` or require direct pool calls only.** For a swap allowlist, the economically relevant actor is the address that receives output tokens (`recipient`). Alternatively, the extension can enforce `sender == tx.origin` to block contract-mediated calls, though this breaks composability.

3. **Cleanest: the pool should forward the original `tx.origin` or a verified initiator field.** The core `swap()` signature would need a `payer` or `initiator` parameter analogous to the `owner`/`sender` split already present on `addLiquidity`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) with msg.sender = router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Check: allowedSwapper[pool][router] == true  → passes
  - Bob's swap executes against pool liquidity, bypassing the allowlist
```

**Call trace:**

```
bob → MetricOmmSimpleRouter.exactInputSingle()
        → MetricOmmPool.swap(recipient, ...)   [msg.sender = router]
            → ExtensionCalling._beforeSwap(sender=router, ...)
                → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                    allowedSwapper[pool][router] == true → no revert
            → _executeSwap(...)                [bob's trade executes]
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

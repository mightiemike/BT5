### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end user, enabling a full allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument it receives from the pool. Because the pool sets `sender = msg.sender` (the immediate caller of `pool.swap()`), any swap routed through `MetricOmmSimpleRouter` presents the router's address as the swapper. If the pool admin allowlists the router — the only way to permit router-mediated swaps for any allowlisted user — every unpermissioned address can bypass the allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← the immediate caller, not the end user
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

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol line 160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, amountSpecified, priceLimitX64,
     packedSlot0Initial, bidPriceX64, askPriceX64, extensionData)
)
```

`SwapAllowlistExtension.beforeSwap` then gates on that `sender` value:

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the router, so `sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

The pool admin faces an impossible choice:

| Router allowlist status | Effect |
|---|---|
| Router **not** allowlisted | All router-mediated swaps revert — even for allowlisted users |
| Router **allowlisted** | Every address can bypass the allowlist by routing through the public router |

There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

Any address can swap on a curated, allowlist-restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). The pool admin cannot enforce per-user swap restrictions while also supporting the standard periphery router. This is a direct allowlist bypass with fund-impacting consequences: pools intended to be restricted to specific counterparties (e.g., KYC'd LPs, protocol-owned addresses, or whitelisted market makers) become fully open to arbitrary swappers.

---

### Likelihood Explanation

The bypass is reachable by any unpermissioned user the moment the pool admin allowlists the router — a natural and expected action for any pool that wants to support the standard periphery swap path for its allowlisted users. The router is a public, permissionless contract. No privileged setup beyond the pool admin's routine configuration is required.

---

### Recommendation

The allowlist check must be keyed to the economically relevant actor — the end user — not the intermediate router. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the extension to trust that the router is the immediate caller (verifiable via `msg.sender` inside the extension).

2. **Check `sender` only when `msg.sender` (the pool's caller) is not a trusted router**: The extension maintains a registry of trusted routers; for router calls it reads the end user from a standardised field in `extensionData`.

The simplest correct fix is option 1: the router appends `abi.encode(msg.sender)` to `extensionData`, and the extension, after confirming `msg.sender` (the pool) is a known pool, decodes the real swapper from `extensionData` when the immediate `sender` is a known router.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is permitted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. `_beforeSwap` is called with `sender = router`.
7. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps on a pool he is not allowlisted for.

If the pool admin does **not** allowlist the router (step 3 omitted), Alice also cannot use the router — the extension checks `allowedSwapper[pool][router]` → `false` → revert. The allowlist is broken in both directions. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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

### Title
SwapAllowlistExtension Allowlist Fully Bypassed via MetricOmmSimpleRouter — Any User Can Swap on Restricted Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the direct caller of `pool.swap()` is the router contract, not the end user. If the pool admin allowlists the router address (the only way to permit router-mediated swaps for legitimate users), every user — including those explicitly excluded from the allowlist — can bypass the per-user gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient, zeroForOne, amountSpecified, priceLimitX64,
    packedSlot0Initial, bidPriceX64, askPriceX64, extensionData
);
```

`ExtensionCalling._beforeSwap` encodes that value as the `sender` field and dispatches it to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist for the calling pool:

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`) calls `pool.swap()`, the pool's `msg.sender` is the router contract address, not the end user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin faces an impossible choice:
- **Do not allowlist the router** → legitimate allowlisted users cannot use the router at all.
- **Allowlist the router** → every user, including those explicitly excluded, can bypass the per-user gate by routing through the router.

The bypass path is:

```
Disallowed user
  → MetricOmmSimpleRouter.exactInputSingle(pool, ...)
      → pool.swap(recipient, ...)   [msg.sender = router]
          → _beforeSwap(sender=router, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                  → allowedSwapper[pool][router] == true  ✓  (bypass)
```

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses (e.g., KYC-verified market makers, institutional counterparties, or protocol-controlled bots). Once the router is allowlisted — the only way to support router-mediated swaps for legitimate users — the restriction is completely nullified. Any address can execute swaps against the pool's liquidity at oracle-derived prices, draining LP principal and fees in ways the pool admin explicitly intended to prevent. The allowlist guard provides zero protection on the router path.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint for the protocol. Pool admins who configure a swap allowlist and also want to support standard router usage will naturally allowlist the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA or contract can call `exactInputSingle` on the router. The precondition (router allowlisted) is the expected production configuration for any allowlisted pool that supports periphery routing.

---

### Recommendation

The extension must gate the actual end user, not the intermediary router. Two sound approaches:

1. **Forward the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap`. The extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `recipient` instead of (or in addition to) `sender`**: For single-hop swaps the recipient is often the end user; however, this breaks for multi-hop paths where intermediate recipients are the router itself.

3. **Separate router-level allowlist from pool-level allowlist**: The router enforces its own per-user check before calling the pool, and the pool allowlist gates only the router address. This requires the router to be a trusted, non-upgradeable contract.

The cleanest fix is option 1: the router appends `abi.encode(msg.sender)` to `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that value when `sender` is a known router.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
//   1. Deploy pool with SwapAllowlistExtension.
//   2. Pool admin allowlists alice (legitimate user) and the router.
//   3. Bob (not allowlisted) calls the router — swap succeeds.

contract BypassPoC {
    IMetricOmmSimpleRouter router;
    address pool;
    address token0;
    address token1;

    function bypass(address bob) external {
        // Bob is NOT in allowedSwapper[pool][bob]
        // Router IS in allowedSwapper[pool][router]
        // Bob routes through the router → extension sees sender=router → passes

        vm.prank(bob);
        router.exactInputSingle(
            IMetricOmmSimpleRouter.ExactInputSingleParams({
                pool: pool,
                tokenIn: token0,
                recipient: bob,
                deadline: block.timestamp + 1,
                amountIn: 1e18,
                amountOutMinimum: 0,
                zeroForOne: true,
                priceLimitX64: 0,
                extensionData: ""
            })
        );
        // Bob's swap executes despite not being on the allowlist.
    }
}
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

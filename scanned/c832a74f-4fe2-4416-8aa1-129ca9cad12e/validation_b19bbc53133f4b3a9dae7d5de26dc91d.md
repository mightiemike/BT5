### Title
`SwapAllowlistExtension` checks the router address instead of the end-user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` intermediates the swap, `sender` is the **router address**, not the actual end-user. A pool admin who allowlists the router to enable router-mediated swaps for their curated users inadvertently opens the gate to every user, because the router is a public, permissionless contract.

---

### Finding Description

**Root cause — wrong actor bound in the hook:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

The pool passes `msg.sender` as `sender` to every extension hook:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that value unchanged:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)   // sender = pool's msg.sender
    )
);
``` [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry-point) calls `pool.swap(...)`, the pool's `msg.sender` is the **router**, so `sender = router_address` reaches the extension. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [4](#0-3) 

This creates an irreconcilable dilemma for any pool admin who deploys a `SwapAllowlistExtension`:

| Router allowlisted? | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| No | ❌ Reverts (router not in list) | ❌ Reverts |
| Yes | ✅ Passes | ✅ Passes — **bypass** |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A pool admin who wants their curated users to be able to use the official router will add the router to the allowlist. Any unprivileged user can then call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting that pool and the `beforeSwap` hook will pass, because the router is allowlisted. The entire per-user access-control layer is silently nullified. Depending on the pool's purpose (e.g., institutional-only pricing, KYC-gated liquidity, restricted-asset pools), this allows unauthorized parties to drain or trade against LP capital that was never intended to be publicly accessible.

---

### Likelihood Explanation

The trigger is a natural, expected admin action: allowlisting the official router so that curated users can access the pool through the standard periphery. No malicious setup is required. Any user who discovers the router is allowlisted can exploit the bypass immediately with a single public call. The router is a deployed, permissionless contract.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **economic actor** (the end-user), not the **call-chain intermediary** (the router). Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` for each hop; the extension decodes and checks it. The pool admin must trust that the router populates this field correctly, which is enforceable by only allowlisting known router versions.

2. **Dedicated router-aware allowlist**: Add a separate mapping `allowedRouter[pool][router]` and, when `sender` is a known router, decode the real user from `extensionData` before performing the allowlist check.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position recipient), which callers of the liquidity adder must supply explicitly and which maps to the economically relevant party.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Admin also allowlists the router so allowedUser can trade via router.
extension.setAllowedToSwap(pool, allowedUser, true);
extension.setAllowedToSwap(pool, address(router), true); // natural admin action

// Attack: attacker (not in allowlist) routes through the router.
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            pool,
        recipient:       attacker,
        zeroForOne:      true,
        amountIn:        1e18,
        amountOutMinimum: 0,
        priceLimitX64:   0,
        deadline:        block.timestamp,
        tokenIn:         token0,
        extensionData:   ""
    })
);
// ↑ succeeds — beforeSwap checks allowedSwapper[pool][router] == true,
//   never inspecting `attacker`.
```

The pool's `swap()` receives `msg.sender = router`; the extension sees `sender = router`; the router is allowlisted; the hook returns the success selector; the swap executes for the non-allowlisted attacker.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

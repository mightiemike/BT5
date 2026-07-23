All four files are confirmed in the repository. Let me verify the exact lines cited in the claim against the actual code.

**Confirmed code facts:**

1. `SwapAllowlistExtension.beforeSwap` (L37): checks `allowedSwapper[msg.sender][sender]` — `msg.sender` is the pool, `sender` is the first argument passed from the pool. [1](#0-0) 

2. `MetricOmmPool.swap` (L230-240): passes `msg.sender` as `sender` to `_beforeSwap` — so when the router calls `pool.swap()`, the router address is what the extension sees as `sender`. [2](#0-1) 

3. `ExtensionCalling._beforeSwap` (L149-177): forwards `sender` unchanged into the `abi.encodeCall` to the extension. [3](#0-2) 

4. `MetricOmmSimpleRouter.exactInputSingle` (L71-80): stores the originating user in transient storage for the payment callback only (`_setNextCallbackContext`), then calls `pool.swap()` directly — making the router `msg.sender` of `pool.swap()`. The originating EOA is never forwarded to the pool or extension. [4](#0-3) 

The same pattern holds for `exactInput` (L103-112), `exactOutputSingle` (L136-137), and `exactOutput` (L165-181). [5](#0-4) 

The vulnerability is confirmed. The claim's inline citations contain minor copy-paste errors (e.g., citing `_beforeAddLiquidity` and `addLiquidity` lines instead of the swap equivalents), but the described mechanism is exactly correct per the actual production code.

---

Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Enabling Full Allowlist Bypass via Public Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the originating EOA. Any pool admin who allowlists the router address — the only way to support router-mediated swaps for their permitted users — renders the allowlist completely ineffective, as any unprivileged user can call the same public router and pass the check.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check at L37:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` is the pool (correct key for the per-pool mapping). `sender` is the first argument, which `ExtensionCalling._beforeSwap` (L149-177) encodes directly from the `sender` parameter passed by `MetricOmmPool.swap`. In `MetricOmmPool.swap` (L230-240), the pool calls `_beforeSwap(msg.sender, ...)`, so `sender` = `msg.sender` of `pool.swap()`.

In `MetricOmmSimpleRouter.exactInputSingle` (L71-80), the router calls `pool.swap(...)` directly. The originating user is stored in transient storage via `_setNextCallbackContext` for the payment callback only — it is never forwarded to the pool or extension. Therefore, the extension receives `sender = router address`, not the originating EOA.

If the pool admin calls `extension.setAllowedToSwap(pool, address(router), true)` to enable router-mediated swaps for their permitted users, the allowlist check becomes `allowedSwapper[pool][router] == true`, which passes for every caller of the public, permissionless router. The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

## Impact Explanation

`SwapAllowlistExtension` is the protocol's mechanism for pool admins to restrict swap access to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers). When the router is allowlisted — the only configuration that supports router-mediated swaps for permitted users — the guard becomes completely ineffective. Any address on the network can execute swaps against the restricted pool by routing through the public router. This is a direct admin-boundary break: an unprivileged path bypasses an access control boundary the pool admin explicitly configured.

## Likelihood Explanation

Any pool admin who wants their allowlisted users to be able to use the standard router must allowlist the router address. There is no other mechanism to support router-mediated swaps while keeping the allowlist active. Once the router is allowlisted, the bypass is trivially reachable by any user with no special privileges, capital requirements, or front-running needed.

## Recommendation

The extension must verify the originating user, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass originating user via `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool. `SwapAllowlistExtension.beforeSwap` decodes and checks this value when `sender` is a recognized router address. This requires a trusted encoding convention between the router and the extension.

2. **Disable router support for allowlisted pools**: Document and enforce (via NatSpec or a runtime check) that pools using `SwapAllowlistExtension` must not allowlist the router and must require direct `pool.swap()` calls only. This eliminates the ambiguity but restricts usability.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER
  - Pool admin calls: extension.setAllowedToSwap(pool, address(router), true)
    (intending to allow permitted users to use the router)
  - Pool admin does NOT call: extension.setAllowedToSwap(pool, bob, true)
    (bob is not a permitted swapper)

Attack:
  - bob calls router.exactInputSingle({pool: pool, tokenIn: token0, ...})
  - Router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
    with msg.sender = router
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - Extension checks: allowedSwapper[pool][router] == true  ← passes
  - Bob's swap executes successfully despite not being on the allowlist

Foundry test outline:
  1. Deploy SwapAllowlistExtension, pool, and MetricOmmSimpleRouter
  2. Admin calls setAllowedToSwap(pool, router, true)
  3. Assert bob (not allowlisted) can call router.exactInputSingle and swap succeeds
  4. Assert bob calling pool.swap() directly reverts with NotAllowedToSwap
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

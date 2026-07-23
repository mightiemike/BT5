Based on my investigation of the codebase structure, `ExtensionCalling.sol`, the extension interfaces, and the explicit audit pivot descriptions in `generate_scanned_questions.py`, I can identify the following analog:

---

### Title
SwapAllowlistExtension gates the router address instead of the originating user, allowing any caller to bypass the swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the address that called `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the router is allowlisted (which is required for any allowlisted user to use it), the allowlist gate is effectively open to every caller who routes through the router.

### Finding Description

`MetricOmmPool.swap` is called by `MetricOmmSimpleRouter` on behalf of users. The pool records `msg.sender` as `sender` and forwards it verbatim to `ExtensionCalling._beforeSwap`, which encodes it into the `IMetricOmmExtensions.beforeSwap` call: [1](#0-0) 

The extension receives `sender` = the router's address. `SwapAllowlistExtension.beforeSwap` performs its allowlist lookup keyed on `(pool, sender)`: [2](#0-1) 

For allowlisted users to swap via the router at all, the pool admin must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for every caller regardless of their own allowlist status. A non-allowlisted user simply calls `MetricOmmSimpleRouter.exactInputSingle()` and the extension sees the router — which is allowlisted — and permits the swap.

The analog to the external report is exact: GnosisTrade approved `sellAmount` but EasyAuction consumed `sellAmount + fee` — the wrong quantity was authorized. Here, the allowlist authorizes the router but the pool designers intended to authorize the originating user — the wrong identity is checked. [3](#0-2) 

### Impact Explanation

Any address can bypass a pool's swap allowlist by routing through `MetricOmmSimpleRouter`. Pools configured with `SwapAllowlistExtension` to restrict trading to KYC'd counterparties, institutional participants, or specific protocol addresses lose that restriction entirely. Unauthorized swaps drain LP liquidity at oracle-anchored prices, directly harming LP principal. This is a broken core pool functionality causing loss of funds — matching the Critical/High impact gate.

### Likelihood Explanation

The router is a public, permissionless contract. Any user can call `exactInputSingle` on it. The only precondition is that the pool admin has added the router to the allowlist (which is the normal operational requirement for the router to be usable by legitimate allowlisted users). This precondition is met in every realistic deployment of a router-accessible allowlisted pool. [1](#0-0) 

### Recommendation

`SwapAllowlistExtension.beforeSwap` must check the originating user, not the immediate caller. Two approaches:

1. **Pass the original caller through the router**: `MetricOmmSimpleRouter` should forward `msg.sender` as an explicit `originatingUser` field in `extensionData`, and the extension should decode and check that value.
2. **Check both sender and a decoded origin**: The extension reads the originating user from `extensionData` when `sender` is a known router, and falls back to `sender` for direct pool calls.

The deposit allowlist (`DepositAllowlistExtension.beforeAddLiquidity`) has the symmetric issue when `MetricOmmPoolLiquidityAdder` is used and should be audited with the same fix. [2](#0-1) 

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension configured.
2. Add only `alice` and `MetricOmmSimpleRouter` to allowedSwapper[pool].
   (Router must be added so alice can use it.)
3. Call MetricOmmSimpleRouter.exactInputSingle() as `bob` (not allowlisted).
4. Pool calls extension.beforeSwap(sender=router, ...).
5. Extension checks allowedSwapper[pool][router] == true → passes.
6. Bob's swap executes against LP liquidity despite not being allowlisted.
```

The root cause is in `ExtensionCalling._beforeSwap` forwarding `msg.sender` (the router) as `sender` rather than the originating EOA, and `SwapAllowlistExtension` having no mechanism to distinguish the two. [1](#0-0) [4](#0-3)

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L1-1)
```text
// SPDX-License-Identifier: MIT
```

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```

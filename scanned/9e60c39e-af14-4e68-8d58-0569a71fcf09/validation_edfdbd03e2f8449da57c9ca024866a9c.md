### Title
Swap Allowlist Bypassed via Router — Any User Can Trade on Curated Pools Through `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument it receives from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks the router's address against the allowlist, not the actual trader's address. Any pool admin who allowlists the router (which they must do to let their allowlisted users trade via the router) simultaneously opens the pool to every non-allowlisted user who routes through the same contract.

---

### Finding Description

`ExtensionCalling._beforeSwap` is called inside `MetricOmmPool.swap` and forwards `msg.sender` as the `sender` argument to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol  lines 149-176
function _beforeSwap(
    address sender,          // = msg.sender of pool.swap()
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
            (sender, recipient, zeroForOne, amountSpecified,
             priceLimitX64, packedSlot0Initial, bidPriceX64, askPriceX64, extensionData)
        )
    );
}
``` [1](#0-0) 

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`. When the call originates from `MetricOmmSimpleRouter`, `msg.sender` at the pool level is the router contract address, not the end user. [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` performs an `allowedSwapper` lookup keyed by `(pool, sender)`. Because `sender` is the router address in the router-mediated path, the extension evaluates the router's allowlist status, not the individual user's. [3](#0-2) 

The `IMetricOmmExtensions.beforeSwap` interface signature confirms `sender` is the only identity field available to the extension for the swap actor: [4](#0-3) 

The extension system provides no separate field for the originating EOA when the call is router-mediated.

---

### Impact Explanation

A pool admin configures `SwapAllowlistExtension` to restrict trading to a curated set of users (e.g., KYC-verified addresses, institutional counterparties). To let those users trade via the supported periphery router, the admin must allowlist the router address. Once the router is allowlisted, every user — including those explicitly excluded from the allowlist — can bypass the restriction by calling `MetricOmmSimpleRouter.exactInputSingle` (or any equivalent router entry point). The curated pool's entire access-control boundary collapses to a single shared router address. Non-allowlisted users gain full swap access, draining liquidity or executing trades the pool was designed to prevent.

---

### Likelihood Explanation

The trigger requires no special privilege. Any public user can call the router. The precondition — the router being allowlisted — is a necessary operational step for any allowlisted user to use the router at all, so it is expected to be present on any production curated pool that supports router-based trading. The bypass is therefore reachable on every such pool without any additional setup.

---

### Recommendation

The `SwapAllowlistExtension` must gate the originating user, not the immediate `msg.sender` of `pool.swap`. Two complementary fixes:

1. **Pass the originating user through the router.** `MetricOmmSimpleRouter` should forward the caller's address as a dedicated field in `extensionData` (or via a separate argument if the interface is extended), and `SwapAllowlistExtension` should decode and check that field instead of `sender`.

2. **Alternatively, check `sender` only when it is not a known router.** The extension can maintain a registry of trusted routers and, when `sender` is a router, require the originating user identity to be embedded in `extensionData` and verified there.

Without one of these changes, any allowlist-protected pool that supports router-mediated swaps is effectively unprotected.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the intended user
  allowedSwapper[pool][router] = true         // admin must set this for alice to use the router

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
      → pool.swap(msg.sender=router, recipient=bob, ...)
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (passes)
        → swap executes for bob
  
Result: bob swaps on a pool he is not allowlisted for.
  The allowlist check passed because it evaluated the router, not bob.
``` [5](#0-4) [2](#0-1)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L281-295)
```text
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
      extensionData
    );
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

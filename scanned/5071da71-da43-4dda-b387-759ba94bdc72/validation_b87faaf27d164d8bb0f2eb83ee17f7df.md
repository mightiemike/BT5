### Title
SwapAllowlistExtension Bypassed via Router: Any User Can Swap on Allowlist-Gated Pools When Router Is Allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool, which equals `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool sees the router as `msg.sender`, so the extension checks the router's allowlist status rather than the original user's. A pool admin who allowlists the router to enable router-mediated swaps for legitimate users inadvertently opens the gate to every user on the network.

---

### Finding Description

`MetricOmmPool.swap` captures `msg.sender` and forwards it as the `sender` argument to every configured extension:

```solidity
// MetricOmmPool.sol lines 230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
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

`ExtensionCalling._beforeSwap` passes that value unchanged to every extension in the configured order:

```solidity
// ExtensionCalling.sol lines 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, amountSpecified,
         priceLimitX64, packedSlot0Initial, bidPriceX64, askPriceX64, extensionData)
    )
);
```

`SwapAllowlistExtension.beforeSwap` (analogous to `DepositAllowlistExtension.beforeAddLiquidity`, which checks `msg.sender` as the pool key and the first named identity parameter as the gated actor) checks `sender` against `allowedSwapper[msg.sender]`. When the user calls `MetricOmmSimpleRouter.exactInput` / `exactOutput`, the router calls `pool.swap(...)`, so `msg.sender` inside the pool is the router address. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants to support router-mediated swaps for their allowlisted users must add the router to the allowlist. Once the router is allowlisted, the check is satisfied for every caller regardless of their individual allowlist status, because the extension never sees the original user's address.

The `onlyPool` guard on `BaseMetricExtension` is correctly inherited by `SwapAllowlistExtension` (unlike `PriceVelocityGuardExtension`, which drops it), so the extension cannot be called directly by an attacker. The bypass is structural: the pool's own forwarding of `msg.sender` is the root cause.

---

### Impact Explanation

Any user who is explicitly excluded from the swap allowlist can execute swaps against the pool by routing through `MetricOmmSimpleRouter`. The pool's liquidity is exposed to every participant on the network, defeating the allowlist's purpose. LP providers who deposited under the assumption that only vetted counterparties could trade against their positions suffer direct loss of principal through adverse selection or targeted extraction. This constitutes broken core pool functionality and direct loss of LP assets above Sherlock thresholds.

---

### Likelihood Explanation

The bypass requires the router to be on the allowlist. A pool admin who deploys a swap-allowlisted pool and also wants to support multi-hop or ETH-wrapping flows through the periphery router has no other option than to allowlist the router. This is a foreseeable and common operational configuration. Once the router is allowlisted, the bypass is unconditional and requires no special privileges or tokens from the attacker.

---

### Recommendation

The extension must gate the original user, not the intermediary. Two complementary fixes:

1. **Pass the original initiator through the router**: `MetricOmmSimpleRouter` should forward the original `msg.sender` inside `extensionData` or a dedicated field, and the extension should decode and check that address when the direct caller is a known router.

2. **Check `sender` only when it is not a router, and require the router to attest the real user**: Alternatively, the pool or extension can maintain a registry of trusted routers; when `sender` is a trusted router, the extension reads the real user from `extensionData` and checks that address instead.

The simplest safe default is to never allowlist the router as a blanket swapper; instead, require every user to call the pool directly or use a router that cryptographically attests the originating address.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]   = true   (alice is a vetted LP counterparty)
  allowedSwapper[pool][router]  = true   (admin adds router to support alice's multi-hop flows)
  allowedSwapper[pool][attacker]= false  (attacker is explicitly excluded)

Attack:
  attacker calls MetricOmmSimpleRouter.exactInput({
      tokens: [tokenA, tokenB],
      pools:  [pool],
      ...
  })

  Router calls pool.swap(attacker_recipient, ...)
  pool.swap captures msg.sender = router
  _beforeSwap(router, ...) → SwapAllowlistExtension.beforeSwap(sender=router, ...)
  Extension checks allowedSwapper[pool][router] → true  ✓
  Swap executes; attacker receives tokenB output

Result:
  Attacker bypasses the allowlist and drains pool liquidity.
  LP providers suffer direct loss of principal.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L1-1)
```text
// SPDX-License-Identifier: MIT
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```

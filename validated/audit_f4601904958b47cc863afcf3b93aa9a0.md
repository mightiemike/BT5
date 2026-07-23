### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against `allowedSwapper[pool][sender]`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so `sender` forwarded to the extension is the router's address — not the original user. If the pool admin allowlists the router (the only way to let any user reach the pool through the router), every non-allowlisted user can bypass the curated allowlist entirely.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInput*()
         → MetricOmmPool.swap(recipient, ..., extensionData)   [msg.sender = router]
              → ExtensionCalling._beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the original user
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router: [3](#0-2) 

**Two broken states result:**

| Router allowlisted? | Effect |
|---|---|
| **Yes** | Every user — including those explicitly denied — can bypass the allowlist by routing through the router |
| **No** | Even allowlisted users cannot use the router; the router is permanently blocked |

Neither state matches the pool admin's intent. The allowlist is designed to gate individual swapper identities, but the router collapses all users into a single identity.

The `DepositAllowlistExtension` does not share this flaw because it gates on the `owner` argument (the economic beneficiary), which the caller supplies explicitly and the pool passes through unchanged: [4](#0-3) 

---

### Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to known counterparties (e.g., to prevent toxic flow or enforce KYC) is fully bypassed the moment the pool admin allowlists the router. Any non-allowlisted address can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against LP positions. LP principal is directly at risk from the unauthorized flow the allowlist was meant to block.

---

### Likelihood Explanation

The router is the primary user-facing entry point documented in the periphery. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address, which simultaneously opens the pool to all users. The bypass requires no special privileges, no flash loans, and no unusual token behavior — only a standard router call.

---

### Recommendation

The extension must check the original user, not the intermediary. Two viable approaches:

1. **Pass the original caller through the router.** The router stores the original `msg.sender` in transient storage before calling the pool, and the pool (or extension) reads it via a callback or a dedicated accessor. The extension then checks `allowedSwapper[pool][originalCaller]`.

2. **Check `sender` in the extension but require the router to forward the real user.** The router encodes the real user address into `extensionData`, and the extension decodes and verifies it (with a signature or trusted-forwarder pattern).

Until fixed, pool admins should not deploy `SwapAllowlistExtension` on pools that are also expected to be reachable through `MetricOmmSimpleRouter`.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   (alice is the intended gated user)
  allowedSwapper[pool][router] = true  (admin must set this so alice can use the router)

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})

  Inside pool.swap:
    msg.sender = router
    _beforeSwap(sender=router, ...)

  Inside SwapAllowlistExtension.beforeSwap:
    allowedSwapper[pool][router] == true  → passes

  Result: bob's swap executes against LP positions despite being denied.
``` [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass Per-User Allowlist via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When `MetricOmmSimpleRouter` is the caller, the pool sets `sender = msg.sender = router`. The extension therefore checks whether the **router** is allowlisted, not the actual end-user. Any user who routes through the router inherits the router's allowlist status, completely bypassing the per-user restriction the pool admin intended to enforce.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is whoever called `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, the router calls `pool.swap(...)`, so `sender = address(router)`.

The allowlist lookup becomes `allowedSwapper[pool][router]`. The pool admin faces an impossible choice:

| Router allowlisted? | Effect |
|---|---|
| Yes | Every user — including non-allowlisted ones — can bypass the restriction by routing through the router |
| No | Legitimate allowlisted users cannot use the router at all |

There is no configuration that simultaneously allows router usage and enforces per-user restrictions, because the extension has no visibility into the actual end-user identity.

The `IMetricOmmExtensions` interface confirms that `sender` and `recipient` are the only actor fields available to `beforeSwap`; no original-caller field is threaded through: [4](#0-3) 

---

### Impact Explanation

A pool admin deploys a curated pool (e.g., KYC-only, institutional-only) and configures `SwapAllowlistExtension` with a specific set of allowlisted addresses. To support normal UX, the admin also allowlists the router. Any non-allowlisted user can then call `MetricOmmSimpleRouter.exactInput/exactOutput`, which calls `pool.swap` with `msg.sender = router`. The extension sees the allowlisted router address and passes the check. The non-allowlisted user trades freely on a pool that was supposed to exclude them — direct policy bypass with fund-level consequences (unauthorized access to pool liquidity, fee extraction, and price impact on restricted pools).

---

### Likelihood Explanation

The router is the primary supported periphery entry point for swaps. Any pool that enables `SwapAllowlistExtension` and also wants users to use the router must allowlist the router, which immediately opens the bypass. The trigger requires no special permissions, no malicious setup, and no non-standard tokens — only a standard router call from any EOA.

---

### Recommendation

The pool must thread the original end-user identity through to the extension. Two viable approaches:

1. **Dedicated originator field**: Add an `originator` parameter to `pool.swap` (set by the router to `msg.sender` before calling the pool) and pass it alongside `sender` in `_beforeSwap` / `IMetricOmmExtensions.beforeSwap`. The allowlist extension then checks `originator` when `sender` is a known router.

2. **Extension-data convention**: Require the router to ABI-encode the actual user address into `extensionData` and have `SwapAllowlistExtension` decode and check it. This is weaker because it relies on the router's cooperation, but avoids an interface change.

The `DepositAllowlistExtension` should be audited for the same pattern with `sender`/`owner` separation on the `MetricOmmPoolLiquidityAdder` path.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]   = true   // alice is KYC'd
  allowedSwapper[pool][bob]     = false  // bob is NOT allowlisted
  allowedSwapper[pool][router]  = true   // router allowlisted so alice can use it

Attack:
  bob calls router.exactInputSingle(pool, ...)
    -> router calls pool.swap(recipient=bob, ...)
       msg.sender at pool = router
    -> _beforeSwap(sender=router, ...)
    -> SwapAllowlistExtension.beforeSwap(sender=router, ...)
       check: allowedSwapper[pool][router] == true  ✓  (passes)
    -> swap executes for bob

Result:
  bob trades on a pool that was supposed to exclude him.
  The allowlist is completely ineffective for router-mediated swaps.
``` [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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

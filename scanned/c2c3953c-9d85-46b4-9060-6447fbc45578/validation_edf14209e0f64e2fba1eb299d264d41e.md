After reading all the key files, I have a clear picture of the codebase. Let me trace the exact bypass path.

In `MetricOmmPool.swap`, the `sender` forwarded to `_beforeSwap` is `msg.sender` — the immediate caller of the pool: [1](#0-0) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap`, `msg.sender` inside the pool is the **router**, not the end user: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[pool][sender]` where `sender` is the router address: [3](#0-2) 

---

### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` (the immediate caller). When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract, not the actual trader. A pool admin who allowlists the router to enable router-mediated swaps for their approved users inadvertently opens the pool to every user on the router, defeating the per-user allowlist entirely.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [4](#0-3) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [5](#0-4) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [6](#0-5) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap`. For a direct call `sender = user` (correct). For a router-mediated call `sender = router` (wrong identity). The extension has no mechanism to decode the actual end user from `extensionData`; it only inspects `sender`.

The pool admin faces an impossible choice:
- **Do not allowlist the router** → allowlisted users cannot use `MetricOmmSimpleRouter` at all.
- **Allowlist the router** → every user on the router bypasses the per-user gate, because `allowedSwapper[pool][router] = true` satisfies the check for any caller.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner`, which is an explicit argument the caller supplies and which the liquidity adder correctly sets to `msg.sender` (the actual depositor): [7](#0-6) [8](#0-7) 

The swap path has no equivalent explicit-user argument; `sender` is always the immediate `msg.sender` of `pool.swap`.

### Impact Explanation
Any non-allowlisted address can execute swaps in a pool that the admin intended to restrict to a specific set of traders. In an oracle-based pool, sophisticated actors who gain unauthorized access can exploit the oracle-derived bid/ask prices to extract value from LPs (adverse selection), directly reducing LP principal. The allowlist is the only on-chain mechanism preventing this; once bypassed, the pool is economically equivalent to an open pool for all router users.

### Likelihood Explanation
High. The pool admin has a natural incentive to allowlist the router: without it, their approved users cannot benefit from multi-hop routing, slippage protection, or deadline enforcement provided by `MetricOmmSimpleRouter`. Any pool that (a) uses `SwapAllowlistExtension` and (b) also allowlists the router — a common and reasonable operational choice — is fully exposed. No attacker capability beyond calling a public router function is required.

### Recommendation
The `SwapAllowlistExtension` must gate on the actual end user, not the immediate pool caller. Two sound approaches:

1. **Explicit user forwarding via `extensionData`**: Have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it (with a signature or trusted-forwarder pattern so the field cannot be spoofed by a direct caller).
2. **Separate router allowlist from user allowlist**: Add a second mapping `allowedRouter` that permits a router to act on behalf of any allowlisted user, and require the router to attest the actual user identity in `extensionData`.

Do not use `tx.origin`; it is vulnerable to phishing via malicious contracts.

### Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension (BEFORE_SWAP_ORDER = extension 1)
  pool admin calls setAllowedToSwap(pool, alice, true)      // allowlist alice
  pool admin calls setAllowedToSwap(pool, router, true)     // allowlist router so alice can use it

Attack (bob, not allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: bob,
      ...
  })

  → router calls pool.swap(bob, ...)
  → pool calls _beforeSwap(msg.sender=router, ...)
  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
  → checks allowedSwapper[pool][router] == true  ✓
  → swap executes for bob despite bob not being allowlisted

Result: bob swaps successfully in a pool he should be barred from.
``` [9](#0-8) [10](#0-9) [11](#0-10)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L78-81)
```text
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateDeltas(deltas);
    return _addLiquidity(pool, msg.sender, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

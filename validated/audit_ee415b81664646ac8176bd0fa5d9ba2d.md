Based on my investigation of the repository, I need to trace the hook plumbing through the allowlist extensions and the pool's extension dispatch to find the analog.

Let me review the key code I found:

**`SwapAllowlistExtension.beforeSwap`** (line 31-41):
```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

**`MetricOmmPool.swap`** (line 230-239): passes `msg.sender` as `sender` to `_beforeSwap`.

**`ExtensionCalling._beforeSwap`** (line 149-177): forwards `sender` verbatim to the extension.

---

### Title
`SwapAllowlistExtension.beforeSwap` gates the router's address rather than the end user, allowing any caller to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, `sender` equals the router's address, not the end user's address. A pool admin who wants to support router-mediated swaps must allowlist the router contract itself. Once the router is allowlisted, every user — including those the admin explicitly did not allowlist — can bypass the individual-user gate by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct for keying by pool) and `sender` is whoever called `pool.swap`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any router entry point), the router calls `pool.swap`, so `sender` = router address. The extension never sees the end user's address. This creates an inescapable dilemma for the pool admin:

- **Do not allowlist the router** → every legitimate user who uses the standard router interface is blocked, even if they are individually allowlisted.
- **Allowlist the router** → every user on the network can bypass the individual-user allowlist by routing through the router, because `allowedSwapper[pool][router] == true` satisfies the guard for all callers.

The same structural flaw applies to `DepositAllowlistExtension.beforeAddLiquidity`, where `sender` is the direct caller of `pool.addLiquidity`. A user routing through `MetricOmmPoolLiquidityAdder` presents the adder's address as `sender`; allowlisting the adder opens deposits to all users. [4](#0-3) 

### Impact Explanation

Any user can execute swaps in a pool that the admin intended to restrict to a specific set of addresses. This is a direct admin-boundary break: an unprivileged actor bypasses an access-control gate that the pool admin explicitly configured. Pools designed for permissioned trading (e.g., institutional-only, KYC-gated, or whitelist-only liquidity programs) are rendered fully open to the public once the router is allowlisted. LP assets in such pools are exposed to trading by actors the pool was designed to exclude.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented entry point for swaps in the periphery layer. Any pool admin who wants their allowlisted users to use the normal interface must allowlist the router. The bypass is therefore triggered by the ordinary, expected configuration of a permissioned pool and requires no special privileges or unusual inputs from the attacker. [3](#0-2) 

### Recommendation

The extension must check the identity of the economic actor, not the proximate caller. Two sound approaches:

1. **Pass the original user through the router**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that value instead of (or in addition to) `sender`.
2. **Check `recipient` for the swap allowlist**: If the pool's design equates the recipient with the authorized trader, check `recipient` rather than `sender`. This must be validated against the pool's economic model.

The same fix must be applied symmetrically to `DepositAllowlistExtension.beforeAddLiquidity`, which should gate on `owner` (the LP-share recipient) rather than `sender` (the proximate caller/payer).

### Proof of Concept

```
Setup:
  1. Deploy a pool with SwapAllowlistExtension configured.
  2. Pool admin calls setAllowedToSwap(pool, alice, true)  — only alice is allowlisted.
  3. Pool admin calls setAllowedToSwap(pool, router, true) — router allowlisted so alice can use it.

Attack:
  4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...).
  5. Router calls pool.swap(recipient=bob, ...) → msg.sender = router.
  6. Pool calls _beforeSwap(sender=router, ...).
  7. Extension checks allowedSwapper[pool][router] == true → passes.
  8. Bob's swap executes successfully despite not being individually allowlisted.

Result:
  - Bob bypasses the per-user allowlist gate.
  - Any user can repeat this, rendering the allowlist ineffective.
``` [3](#0-2) [5](#0-4) [2](#0-1)

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

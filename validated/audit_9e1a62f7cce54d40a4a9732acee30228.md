### Title
`SwapAllowlistExtension.beforeSwap` gates on `sender` (the router/caller) instead of `recipient` (the actual trader), while `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` — allowing any user to bypass a curated pool's swap allowlist by routing through `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension` and `DepositAllowlistExtension` apply their allowlist checks to **different actors**. The deposit extension correctly checks `owner` (the position owner — the actual user). The swap extension checks `sender` (the direct `msg.sender` to the pool — the router contract). When a user swaps through `MetricOmmSimpleRouter`, `sender` = router address, not the user. If the router is allowlisted (or if the admin allowlists it to enable router-based swaps), every user — including those not individually allowlisted — can bypass the per-user swap gate.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` ignores the `sender` parameter and checks `owner`: [1](#0-0) 

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

`SwapAllowlistExtension.beforeSwap` ignores the `recipient` parameter and checks `sender`: [2](#0-1) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

The pool passes `sender` = `msg.sender` of the pool call through `ExtensionCalling._beforeSwap`: [3](#0-2) 

When a user swaps via `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the user. The user is the `recipient`. The allowlist check therefore evaluates the router's address, not the user's address.

The asymmetry is confirmed by the integration test setup, where the deposit allowlist is configured with `_getCallerAddress(0)` (the `TestCaller` intermediary address), and the deposit extension checks `owner` (also the `TestCaller` address passed explicitly as the position owner): [4](#0-3) 

This means:
- **Deposit allowlist** → gates on the position owner (the user). Correct.
- **Swap allowlist** → gates on the direct pool caller (the router). Wrong actor for per-user curation.

A pool admin who intends to restrict swaps to a specific set of users will configure `allowedSwapper[pool][userAddress] = true`. But when those users (and any other users) call through `MetricOmmSimpleRouter`, `sender` = router. The router is not in the allowlist → all router-based swaps are blocked. To re-enable router swaps, the admin must allowlist the router address — which then opens the gate to **every** user, including those the admin intended to exclude.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of counterparties (e.g., KYC'd addresses, institutional partners) cannot enforce that restriction when users interact through the supported `MetricOmmSimpleRouter` periphery path. Any user can bypass the per-user allowlist by routing through the router if the router address is allowlisted. This is a direct policy bypass on a core access-control extension, allowing unauthorized parties to trade in pools that were explicitly configured to exclude them. Depending on pool design, this can result in unauthorized price impact, unauthorized fee extraction, or violation of regulatory/compliance constraints that the allowlist was meant to enforce.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entrypoint for end users. Any pool that:
1. Deploys `SwapAllowlistExtension` to gate swaps, **and**
2. Allowlists the router (or any shared intermediary) to enable normal router-based trading

is immediately vulnerable. This is the expected operational pattern for a curated pool that still wants to support the standard periphery. The admin has no way to simultaneously allowlist the router and restrict individual users — the two goals are mutually exclusive under the current `sender`-based check.

---

### Recommendation

Change `SwapAllowlistExtension.beforeSwap` to check `recipient` instead of `sender`, mirroring the `owner`-based check in `DepositAllowlistExtension`:

```solidity
// Before (checks the router/caller — wrong actor):
function beforeSwap(address sender, address, bool, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {

// After (checks the actual trader — correct actor):
function beforeSwap(address, address recipient, bool, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
```

This aligns the swap allowlist with the deposit allowlist's actor model: both gate on the economic beneficiary of the action (position owner for deposits, output recipient for swaps), regardless of which intermediary contract relays the call.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Admin allowlists `allowedSwapper[pool][routerAddress] = true` (necessary to allow any router-based swaps).
3. Admin does **not** allowlist `allowedSwapper[pool][malloryAddress]`.
4. Mallory calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool with herself as `recipient`.
5. The pool calls `extension.beforeSwap(router, mallory, ...)`.
6. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Mallory successfully swaps in a pool that was configured to exclude her.

The deposit allowlist does not have this bypass: even if Mallory routes through `MetricOmmPoolLiquidityAdder`, the extension checks `owner` (Mallory's address), which is not allowlisted, and the deposit reverts correctly. [2](#0-1) [1](#0-0)

### Citations

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L55-60)
```text
  function test_blocksSwapWhenSwapperNotAllowed() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);

    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
```

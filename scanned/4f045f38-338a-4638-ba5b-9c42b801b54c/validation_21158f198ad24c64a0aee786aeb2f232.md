### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing any user to bypass per-user swap allowlists via the periphery router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter (the direct `msg.sender` of the pool's `swap` call) rather than the `recipient` (the actual economic beneficiary). When users route swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the individual user. Any non-allowlisted user can therefore bypass a per-user swap allowlist by routing through the periphery router, provided the router itself is allowlisted — which is a prerequisite for any legitimate user to use the supported periphery path.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` is defined as:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

The `sender` argument is the first parameter forwarded by `ExtensionCalling._beforeSwap`, which is `msg.sender` of the pool's `swap` call:

```solidity
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)
    )
);
``` [2](#0-1) 

When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender` of the pool call, so `sender = router`. The extension therefore checks whether the **router** is allowlisted, not whether the **individual user** is allowlisted.

This is structurally inconsistent with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly checks `owner` — the actual position beneficiary — and ignores `sender`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

The `IMetricOmmExtensions.beforeSwap` interface exposes both `sender` and `recipient`:

```solidity
function beforeSwap(
    address sender,
    address recipient,
    ...
) external returns (bytes4);
``` [4](#0-3) 

`SwapAllowlistExtension` silently discards `recipient` (the actual user receiving output tokens) and checks `sender` instead.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to KYC'd or approved addresses faces a forced dilemma:

1. **Allowlist individual users** → those users cannot swap through the router (router address fails the check), breaking the supported periphery path entirely.
2. **Allowlist the router** → every user, including non-approved ones, can bypass the per-user gate by routing through the periphery.

In scenario 2, the allowlist is completely ineffective: any unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`, the pool sees `sender = router`, the extension passes, and the swap executes. The curated pool's access control is silently voided. This constitutes a direct policy bypass with fund-impacting consequences (disallowed users trade on a pool that should be restricted).

---

### Likelihood Explanation

- The `SwapAllowlistExtension` is a production periphery contract, not a test mock.
- The `MetricOmmSimpleRouter` is the documented, supported swap entrypoint for EOAs.
- Any pool admin who configures per-user swap allowlisting and expects the router to work is affected.
- No special privileges, flash loans, or non-standard tokens are required — a plain router call suffices.

---

### Recommendation

Replace the `sender` check with `recipient` (the actual swap beneficiary), mirroring how `DepositAllowlistExtension` uses `owner`:

```solidity
// Before (wrong actor):
function beforeSwap(address sender, address, ...)
    ...
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {

// After (correct actor):
function beforeSwap(address, address recipient, ...)
    ...
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
``` [1](#0-0) 

This aligns the swap allowlist with the deposit allowlist's design (gate the economic beneficiary, not the routing intermediary) and ensures the policy is enforced identically whether the user calls the pool directly or through the router.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Admin calls `swapExtension.setAllowedToSwap(pool, router, true)` — necessary for any user to swap via the periphery.
3. Admin does **not** call `setAllowedToSwap(pool, attacker, true)`.
4. Attacker (non-allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, attacker_as_recipient, ...)`.
5. Pool calls `_beforeSwap(router, attacker, ...)`.
6. Extension checks `allowedSwapper[pool][router]` → `true` → passes.
7. Swap executes. Attacker receives output tokens. Allowlist is bypassed.

The `FullMetricExtensionTest` confirms the actor binding: the test allowlists `callers[0]` (the direct pool caller) and calls the pool through `callers[0]`, not through the router — precisely because routing through the router would break the check. [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```

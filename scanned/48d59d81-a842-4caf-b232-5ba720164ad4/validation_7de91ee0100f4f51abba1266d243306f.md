### Title
`DepositAllowlistExtension` gates on `owner` instead of `sender`, allowing any non-allowlisted caller to bypass the deposit guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as "Gates `addLiquidity` by depositor address, per pool." However, its `beforeAddLiquidity` hook silently ignores the `sender` parameter (the actual caller who provides tokens) and instead checks the `owner` parameter (the position beneficiary). Any non-allowlisted address can bypass the guard by passing an allowlisted address as `owner`, depositing tokens into a restricted pool without authorization.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the before-hook with both the real caller and the requested position owner:

```solidity
// MetricOmmPool.sol
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

The hook signature delivers `sender` as the first argument and `owner` as the second:

```solidity
// ExtensionCalling.sol
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` discards `sender` (first parameter is unnamed) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual swapper):

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [4](#0-3) 

The two sibling allowlist extensions are structurally inconsistent: the swap guard checks the actor (`sender`), while the deposit guard checks the beneficiary (`owner`).

---

### Impact Explanation

A pool configured with `DepositAllowlistExtension` to restrict who may deposit tokens receives no protection against unauthorized depositors. Any address not on the allowlist can call:

```
pool.addLiquidity(allowlisted_address, salt, deltas, callbackData, extensionData)
```

The guard passes because `allowlisted_address` is allowlisted. The caller provides tokens via the swap callback (tokens leave the caller's wallet and enter the pool), and the position is recorded under `allowlisted_address`. The pool's LP composition and fee-share distribution are altered by an actor the pool admin explicitly excluded. Because `removeLiquidity` enforces `msg.sender == owner`, the unauthorized depositor cannot reclaim the tokens — they are permanently credited to the allowlisted address — but the pool has accepted liquidity from an unauthorized source, defeating the access-control invariant the extension is designed to enforce.

---

### Likelihood Explanation

The bypass requires only a standard `addLiquidity` call with a valid allowlisted address as `owner`. No special privileges, flash loans, or oracle manipulation are needed. Any address that knows one allowlisted LP address (publicly readable from `allowedDepositor`) can execute the bypass in a single transaction.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual caller/depositor) instead of `owner`, mirroring the pattern used in `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is instead to restrict who may *own* a position (rather than who may *fund* one), the NatSpec and mapping names (`allowedDepositor`, `AllowedToDepositSet`) must be updated to reflect that, and the `addLiquidity` caller must separately be validated.

---

### Proof of Concept

1. Pool `P` is deployed with `DepositAllowlistExtension` as a before-add-liquidity hook.
2. Pool admin calls `setAllowedToDeposit(P, alice, true)`. Bob is **not** allowlisted.
3. Bob calls `P.addLiquidity(alice, salt, deltas, callbackData, "")`.
4. `beforeAddLiquidity` is invoked with `sender = bob`, `owner = alice`.
5. The check evaluates `allowedDepositor[P][alice] == true` → passes.
6. Bob's tokens are pulled via callback; the position is minted under `alice`.
7. Bob has successfully deposited into a pool that was supposed to exclude him. [3](#0-2)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-39)
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
```

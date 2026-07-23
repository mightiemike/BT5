### Title
`DepositAllowlistExtension.beforeAddLiquidity` Gates on `owner` Instead of `sender`, Allowing Any Unlisted Depositor to Bypass the Allowlist via `MetricOmmPoolLiquidityAdder` - (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual caller/payer) and checks only `owner` (the position owner). Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` lets any `msg.sender` specify an arbitrary `owner`, any unlisted address can bypass the deposit allowlist by naming a listed address as `owner`.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two identity parameters from the pool: `sender` (the address that called `pool.addLiquidity`, i.e. the actual depositor/payer) and `owner` (the position owner recorded in pool storage). The implementation silently drops `sender` and checks only `owner`:

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

The pool passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner`:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` allows any `msg.sender` (Alice, the payer) to specify an arbitrary `owner` (Bob, the position holder):

```solidity
// MetricOmmPoolLiquidityAdder.sol L56-68
function addLiquidityExactShares(
    address pool, address owner, uint80 salt, LiquidityDelta calldata deltas,
    uint256 maxAmountToken0, uint256 maxAmountToken1, bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
}
``` [3](#0-2) 

The call chain when Alice (unlisted) deposits for Bob (listed):

1. Alice calls `LiquidityAdder.addLiquidityExactShares(pool, bob, ...)`
2. Adder calls `pool.addLiquidity(bob, salt, deltas, abi.encode(KIND_PAY), extensionData)`
3. Pool calls `_beforeAddLiquidity(msg.sender=adder, owner=bob, ...)`
4. Extension checks `allowedDepositor[pool][bob]` → Bob is listed → **passes**
5. Alice's tokens are pulled and deposited into Bob's position

Alice (the actual payer) is never checked. The allowlist check on `owner` is trivially satisfied by naming any listed address as the position owner.

The `_validateOwner` check only rejects `address(0)`:

```solidity
// MetricOmmPoolLiquidityAdder.sol L247-249
function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
}
``` [4](#0-3) 

The contract's own NatDoc confirms the intent is to gate the depositor, not the owner:

```solidity
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
``` [5](#0-4) 

The checked identity (`owner`) is structurally decoupled from the paying identity (`sender`/payer) by design of the periphery router.

---

### Impact Explanation

Any address not on the allowlist can deposit into a restricted pool by routing through `MetricOmmPoolLiquidityAdder` and naming any allowlisted address as `owner`. The unlisted depositor's tokens enter the pool and the listed address receives the LP position. The deposit allowlist — the sole access-control gate on the `addLiquidity` path — is fully bypassed. Pools configured as permissioned (e.g., KYC-gated, institution-only, or protocol-controlled liquidity pools) accept unauthorized deposits, breaking the core pool invariant that only approved parties may provide liquidity.

---

### Likelihood Explanation

The bypass requires no special privileges. Any externally-owned account can call `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` with a listed owner. The listed owner's address is readable from on-chain events (`AllowedToDepositSet`). The `MetricOmmPoolLiquidityAdder` is a public periphery contract. No admin cooperation, malicious token, or unusual setup is required.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual depositor/payer) instead of `owner`:

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

This aligns with the `SwapAllowlistExtension` pattern, which correctly checks `sender`:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [6](#0-5) 

---

### Proof of Concept

```
Setup:
  - Pool deployed with DepositAllowlistExtension in BEFORE_ADD_LIQUIDITY_ORDER
  - Admin calls setAllowedToDeposit(pool, bob, true)   // Bob is listed
  - Alice is NOT listed

Attack:
  1. Alice calls:
       LiquidityAdder.addLiquidityExactShares(pool, bob, salt, deltas, maxAmt0, maxAmt1, "")
  2. Adder calls:
       pool.addLiquidity(bob, salt, deltas, abi.encode(KIND_PAY), "")
  3. Pool calls:
       _beforeAddLiquidity(sender=LiquidityAdder, owner=bob, ...)
  4. Extension evaluates:
       allowedDepositor[pool][bob] == true  →  no revert
  5. LiquidityLib.addLiquidity mints shares to bob's position
  6. Callback pulls Alice's tokens into the pool

Result:
  - Alice (unlisted) successfully deposited into a restricted pool
  - Bob's position is credited; Alice paid
  - The allowlist check on Alice was never performed
```

The `test_exactShares_canAddOnBehalfOfAnotherOwner` test in the periphery suite already demonstrates the owner/payer split works end-to-end: [7](#0-6)

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-13)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
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

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
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

**File:** metric-periphery/test/MetricOmmPoolLiquidityAdder.t.sol (L211-220)
```text
  function test_exactShares_canAddOnBehalfOfAnotherOwner() public {
    LiquidityDelta memory d = _deltaAbovePrice(4, 10_000);
    address bob = makeAddr("bob");

    vm.prank(alice);
    helper.addLiquidityExactShares(address(pool), bob, 1, d, type(uint256).max, type(uint256).max, "");

    uint256 bobShares = stateView.positionBinShares(address(pool), bob, 1, int8(4));
    assertGt(bobShares, 0);
  }
```

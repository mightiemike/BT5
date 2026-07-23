### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Actor to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` discards the `sender` parameter (the actual caller/token-payer) and instead validates `owner` (the position recipient) against the allowlist. Because `owner` is a caller-supplied argument that can be set to any allowed address, any unprivileged actor can bypass the deposit gate entirely by nominating an allowed address as `owner` while paying the tokens themselves.

---

### Finding Description

The pool's `addLiquidity` entry point passes two distinct addresses to every `beforeAddLiquidity` hook:

- `sender` = `msg.sender` of the `addLiquidity` call (the actual caller and token-payer via callback)
- `owner` = the position recipient, a free argument supplied by the caller [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both: [2](#0-1) 

The `IMetricOmmExtensions` interface exposes both as named parameters: [3](#0-2) 

`DepositAllowlistExtension.beforeAddLiquidity` silently drops `sender` (unnamed first parameter) and only checks `owner`: [4](#0-3) 

This is the wrong address. `owner` is a free argument the caller controls; `sender` is the address that actually pays the tokens and initiates the deposit.

Contrast with `SwapAllowlistExtension`, which correctly checks `sender`: [5](#0-4) 

The `MetricOmmPoolLiquidityAdder` explicitly supports an `owner ≠ msg.sender` pattern, making the bypass trivially accessible from the standard periphery path: [6](#0-5) 

The payer is always `msg.sender` of the adder call, stored in transient context, and never forwarded to the extension check: [7](#0-6) 

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism to restrict who can inject liquidity. With this bug the guard is completely ineffective:

1. Any unprivileged actor (Alice, not on the allowlist) calls `addLiquidityExactShares(pool, owner=Bob, ...)` where Bob is an allowed address.
2. The extension checks `allowedDepositor[pool][Bob]` → passes.
3. Alice's tokens are pulled from Alice via the modify-liquidity callback and deposited into the pool.
4. Bob receives the LP position shares and can immediately call `removeLiquidity` to withdraw Alice's tokens.

Alice and Bob can collude: Alice injects tokens into a restricted pool, Bob withdraws them, splitting the proceeds. More broadly, any actor can force arbitrary token amounts into a pool whose admin intended to restrict deposits, breaking the pool's access-control invariant and potentially manipulating bin balances or pool composition in ways the admin did not authorize.

This matches the allowed impact gate criterion: **admin-boundary break — factory/pool admin access control is bypassed by an unprivileged path**.

---

### Likelihood Explanation

- Exploitable by any EOA or contract with no special privileges.
- Reachable through the standard `MetricOmmPoolLiquidityAdder` periphery (the intended user-facing entry point), which explicitly supports `owner ≠ msg.sender`.
- Requires only one colluding allowed address (`owner`) to extract value; the attacker does not need to be on the allowlist at all.
- The bypass is a single-call, no-setup attack.

---

### Recommendation

In `DepositAllowlistExtension.beforeAddLiquidity`, replace the check on `owner` with a check on `sender` (the actual caller/payer), matching the pattern used by `SwapAllowlistExtension`:

```solidity
// WRONG (current):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}

// CORRECT (fix):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

If the intended semantic is "the position owner must be allowed" (e.g., for a whitelist of LP recipients), both `sender` and `owner` should be checked. The current single-field check on `owner` alone is insufficient to gate who injects tokens.

---

### Proof of Concept

```
Setup:
  - Pool configured with DepositAllowlistExtension
  - allowedDepositor[pool][Bob] = true
  - Alice is NOT on the allowlist

Attack (via MetricOmmPoolLiquidityAdder):
  vm.prank(Alice);
  liquidityAdder.addLiquidityExactShares(
      pool,
      owner = Bob,   // Bob is allowed → check passes
      salt,
      deltas,
      maxAmount0,
      maxAmount1,
      ""
  );
  // Extension checks allowedDepositor[pool][Bob] → true → no revert
  // Alice's tokens are pulled from Alice via callback
  // Bob receives the LP position

  vm.prank(Bob);
  pool.removeLiquidity(Bob, salt, deltas, "");
  // Bob withdraws Alice's tokens
```

Alice's tokens entered the pool despite Alice not being on the allowlist. The deposit allowlist provided zero protection against Alice. [4](#0-3) [6](#0-5) [2](#0-1)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L188-195)
```text
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
```

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-21)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L183-207)
```text
  function _addLiquidity(
    address pool,
    address positionOwner,
    uint80 salt,
    LiquidityDelta memory deltas,
    address payer,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
    ) {
      amount0Added = a0;
      amount1Added = a1;
      _clearPayContext();
    } catch (bytes memory reason) {
      _clearPayContext();
      assembly ("memory-safe") {
        revert(add(reason, 32), mload(reason))
      }
    }
  }
```

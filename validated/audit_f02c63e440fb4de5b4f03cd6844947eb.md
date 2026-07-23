### Title
`DepositAllowlistExtension` checks caller-controlled `owner` instead of actual `sender`, allowing any non-allowlisted address to bypass the deposit guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` ignores the `sender` argument (the actual `msg.sender` of the `addLiquidity` call) and instead gates access on the `owner` argument, which is a free parameter supplied by the caller. Any non-allowlisted address can bypass the guard by passing any allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` address and passes both `msg.sender` (as `sender`) and `owner` to the `beforeAddLiquidity` hook: [1](#0-0) 

The extension hook signature receives both: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the first `address` argument (`sender`) and only checks `owner`: [3](#0-2) 

Because `owner` is a free parameter in `addLiquidity`, any caller can set it to any allowlisted address. The extension then evaluates `allowedDepositor[pool][owner]` against the allowlisted address and passes, even though the actual depositor (`sender`) is not allowlisted.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual caller): [4](#0-3) 

---

### Impact Explanation

The `DepositAllowlistExtension` is the sole on-chain mechanism for restricting who may provide liquidity to a pool. Bypassing it means:

- Any non-allowlisted address can deposit tokens into a pool configured as access-restricted (private pool, KYC-gated pool, etc.).
- The pool admin's allowlist configuration is completely ineffective; the guard is broken for every pool using this extension.
- A non-allowlisted depositor can create positions owned by an allowlisted address without that address's consent, creating unsolicited LP positions (griefing) and violating the pool's intended access model.
- Tokens flow into the pool from an unauthorized source, breaking the admin-boundary invariant that only approved depositors may contribute liquidity.

---

### Likelihood Explanation

- The `addLiquidity` function is permissionlessly callable by any address.
- The `owner` parameter is documented as a free input with no on-chain restriction.
- No additional privilege or special condition is required; a single transaction suffices.
- Any pool deploying `DepositAllowlistExtension` with a non-empty allowlist is immediately vulnerable.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual caller) instead of `owner` (the caller-controlled position recipient):

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This mirrors the correct pattern already used in `SwapAllowlistExtension`, which checks `sender` (the actual swap initiator). [5](#0-4) 

---

### Proof of Concept

```
Setup:
  - Pool deployed with DepositAllowlistExtension
  - Alice (0xAlice) is allowlisted: allowedDepositor[pool][alice] = true
  - Bob (0xBob) is NOT allowlisted

Attack:
  1. Bob calls pool.addLiquidity(
         owner    = alice,   // allowlisted address
         salt     = 0,
         deltas   = <valid LiquidityDelta>,
         callbackData = <Bob pays tokens in callback>,
         extensionData = ""
     )

  2. Pool calls _beforeAddLiquidity(msg.sender=Bob, owner=alice, ...)

  3. Extension evaluates:
       allowedDepositor[pool][alice] == true  →  check passes

  4. Bob's tokens enter the pool; position is recorded under (alice, 0).

Result:
  - Bob (non-allowlisted) successfully deposited to a restricted pool.
  - The DepositAllowlistExtension guard was completely bypassed.
  - Alice holds an unsolicited LP position funded by Bob.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-195)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
```

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-20)
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

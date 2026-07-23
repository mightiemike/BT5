### Title
`DepositAllowlistExtension` Checks Caller-Supplied `owner` Instead of Actual `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the caller-supplied `owner` (position recipient) against the allowlist instead of the actual `sender` (the address that called `addLiquidity`). Because `owner` is a free parameter chosen by the caller, any unlisted address can bypass the deposit allowlist by setting `owner` to any allowlisted address.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` parameter (the address that will receive the LP position) and passes both `msg.sender` and `owner` to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both as distinct arguments — `sender` (the real caller) and `owner` (the position recipient): [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first (unnamed, discarded) argument and `owner` as its second. The allowlist check is performed only on `owner`: [3](#0-2) 

Because `owner` is freely chosen by the caller, any unlisted address B can call `pool.addLiquidity(owner = A, ...)` where A is any allowlisted address. The extension sees `allowedDepositor[pool][A] == true` and permits the call. B provides the tokens via the swap callback; A receives the LP shares. The allowlist is completely circumvented.

Note that `SwapAllowlistExtension.beforeSwap` does not share this flaw — it correctly checks `sender` (the actual swap caller): [4](#0-3) 

---

### Impact Explanation

The deposit allowlist is the pool admin's mechanism to restrict which addresses may add liquidity (e.g., KYC-gated or institutional-only pools). Bypassing it means:

1. **Broken access control**: Any unlisted address can deposit into a restricted pool, violating the pool admin's explicit configuration.
2. **Forced LP positions**: An attacker can push unwanted LP positions onto allowlisted addresses without their consent. The allowlisted address must actively call `removeLiquidity` to recover their tokens (and `removeLiquidity` enforces `msg.sender == owner`, so only the allowlisted address can remove it).
3. **Pool state manipulation**: An unlisted address can shift bin liquidity distribution in a restricted pool, potentially affecting swap prices, stop-loss watermarks, or price velocity guard state in ways the pool admin did not intend.

---

### Likelihood Explanation

Exploitation requires only a standard `addLiquidity` call with `owner` set to any known allowlisted address (e.g., the pool admin, a known LP, or any address visible on-chain). No special privileges, flash loans, or oracle manipulation are needed. Any address can trigger this at any time.

---

### Recommendation

Replace the `owner` check with a `sender` check in `beforeAddLiquidity`. The first (currently unnamed) parameter is the actual depositor:

```solidity
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

This mirrors the correct pattern already used in `SwapAllowlistExtension`, which checks `sender` (the actual swap initiator).

---

### Proof of Concept

```
Setup:
  - Pool P has DepositAllowlistExtension configured.
  - allowAllDepositors[P] = false
  - allowedDepositor[P][Alice] = true
  - Bob is NOT in the allowlist.

Attack:
  1. Bob calls P.addLiquidity(owner = Alice, salt, deltas, callbackData, extensionData)
  2. Pool calls DepositAllowlistExtension.beforeAddLiquidity(sender=Bob, owner=Alice, ...)
  3. Extension checks: allowedDepositor[P][Alice] == true → no revert
  4. LiquidityLib.addLiquidity credits LP shares to Alice
  5. Pool calls Bob's metricOmmSwapCallback; Bob transfers tokens to the pool
  6. Alice now holds an LP position she did not request; Bob has bypassed the allowlist

Result:
  - Bob successfully deposited into a restricted pool.
  - Alice holds an unwanted LP position (she must call removeLiquidity herself to exit).
  - The deposit allowlist invariant is broken.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
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
  }
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

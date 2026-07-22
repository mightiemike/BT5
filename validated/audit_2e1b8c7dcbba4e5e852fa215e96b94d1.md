### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is the production guard that gates `addLiquidity` on curated pools. It checks the `owner` parameter (the position recipient) against the allowlist, but silently drops the `sender` parameter (the actual token provider). Because `MetricOmmPool.addLiquidity` imposes no `msg.sender == owner` constraint, any unprivileged caller can bypass the allowlist entirely by supplying an allowlisted address as `owner`.

---

### Finding Description

**Wrong-actor binding in `DepositAllowlistExtension.beforeAddLiquidity`**

The extension's guard function ignores the first argument (`sender`) and gates on `owner`: [1](#0-0) 

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`msg.sender` inside the extension is the pool (the caller of the hook). So the check is `allowedDepositor[pool][owner]`.

**`MetricOmmPool.addLiquidity` imposes no `msg.sender == owner` constraint** [2](#0-1) 

```solidity
function addLiquidity(
    address owner,          // ← caller-supplied; no check that msg.sender == owner
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    ...
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
        _liquidityContext(), owner, salt, deltas, callbackData, ...
    );
    ...
}
```

The hook receives `sender = msg.sender` (the actual token provider) and `owner` (the position recipient) as separate arguments: [3](#0-2) 

The extension discards `sender` and only validates `owner`. Because `owner` is a free caller-supplied parameter with no ownership proof, any caller can pass an allowlisted address as `owner` to satisfy the guard, while the actual token pull happens against `msg.sender` (the unauthorized caller) via the liquidity callback.

Compare with `removeLiquidity`, which correctly enforces `msg.sender == owner`: [4](#0-3) 

No equivalent guard exists in `addLiquidity`.

---

### Impact Explanation

1. **Deposit allowlist fully bypassed.** Any unprivileged address can deposit into a curated pool by passing any allowlisted address as `owner`. The pool admin's curation policy (e.g., KYC, institutional-only) is rendered ineffective.

2. **Unauthorized liquidity injection into another user's position.** Because the position is keyed by `(owner, salt)`, an attacker can add liquidity to an existing LP's position in bins the LP did not choose. The LP cannot prevent this; they can only remove it after the fact, potentially after suffering impermanent loss in the injected bins.

3. **Pool receives tokens from unauthorized sources.** The pool's token balances grow from callers who were explicitly excluded by the pool admin, violating the invariant that only allowlisted depositors contribute to pool state.

---

### Likelihood Explanation

- **Trivially reachable.** `addLiquidity` is a public external function. No special role, permit, or setup is required.
- **Allowlisted addresses are discoverable on-chain** via `allowedDepositor` mapping events or direct storage reads.
- **No economic cost to the attacker** beyond gas; the attacker provides tokens for the victim's position, so the attack is self-funded only if the attacker has a griefing motive. For the allowlist bypass alone (depositing into a curated pool for one's own benefit), the attacker would need to use their own address as `owner` — but that address is not allowlisted, so the check would fail. The bypass only works when the attacker uses *someone else's* allowlisted address as `owner`, meaning the attacker bears the token cost and the allowlisted user receives the position. This limits the financial incentive but does not eliminate the curation failure or the griefing vector.

---

### Recommendation

Check `sender` (the actual token provider) instead of `owner` (the position recipient):

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This aligns with how `SwapAllowlistExtension.beforeSwap` correctly gates on `sender`: [5](#0-4) 

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true   // alice is allowlisted
  allowedDepositor[pool][bob]   = false  // bob is NOT allowlisted

Attack (bob calls directly):
  pool.addLiquidity(
      owner        = alice,   // allowlisted address — satisfies the guard
      salt         = 99,
      deltas       = <bins bob wants to inject>,
      callbackData = <bob's token approval>,
      extensionData = ""
  )

Extension check:
  allowedDepositor[pool][alice] == true  →  guard passes

Outcome:
  - Bob's tokens are pulled via the liquidity callback (bob is msg.sender)
  - Alice's position (alice, 99) receives the injected liquidity
  - Bob has deposited into a curated pool without being allowlisted
  - Alice's position now contains bins she never chose; she must remove them
    to avoid unintended exposure
```

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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
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

### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual caller of `addLiquidity`) and instead gates on `owner` (the LP-position recipient). Because `owner` is a free caller-controlled argument with no other on-chain constraint, any unlisted address can bypass the allowlist by nominating an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` forwards them positionally as `(sender, owner, ...)`:

```solidity
// ExtensionCalling.sol lines 88-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

The interface confirms the semantic: first parameter = `sender` (the depositing caller), second = `owner` (the LP-position holder).

`DepositAllowlistExtension.beforeAddLiquidity` drops `sender` entirely (unnamed `address`) and checks only `owner`:

```solidity
// DepositAllowlistExtension.sol lines 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`owner` is a free parameter supplied by the caller of `addLiquidity`; there is no pool-level constraint that `msg.sender == owner` for deposits (only `removeLiquidity` enforces that). Therefore any unlisted `msg.sender` can pass the guard by supplying any allowlisted address as `owner`.

The admin-facing API (`setAllowedToDeposit`, `isAllowedToDeposit`) uses the term **depositor**, and the event is `AllowedToDepositSet(pool, depositor, allowed)` — all naming confirms the intent is to gate the depositing actor, not the LP-position recipient.

---

### Impact Explanation

The `DepositAllowlistExtension` is the sole mechanism a pool admin has to restrict who may add liquidity. With the check on the wrong parameter:

- Any address — including addresses the pool admin explicitly excluded — can call `addLiquidity(owner = <allowlisted_address>, ...)` and succeed.
- The pool admin's allowlist is rendered completely ineffective; the extension provides no actual access control over who deposits tokens into the pool.
- A colluding pair (unlisted depositor + allowlisted owner) can freely add liquidity: the unlisted actor pays the tokens, the allowlisted address holds the shares and can later `removeLiquidity` to return proceeds. This is a complete allowlist bypass with no on-chain friction.
- Pools relying on this extension for compliance, KYC gating, or LP-composition control have a broken invariant from deployment.

---

### Likelihood Explanation

- Triggering requires only a standard `addLiquidity` call with a chosen `owner` value — no special permissions, flash loans, or reentrancy.
- The bypass is deterministic and requires zero gas overhead beyond a normal deposit.
- Any pool that deploys with `DepositAllowlistExtension` and a non-trivial allowlist is immediately vulnerable.

---

### Recommendation

Replace the unnamed first parameter with `sender` and gate on it instead of `owner`:

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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the swap caller) rather than `recipient`.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only `Alice`.
2. `Bob` (not allowlisted) calls:
   ```solidity
   pool.addLiquidity(
       owner = Alice,   // allowlisted — passes the guard
       salt  = 0,
       deltas = ...,    // Bob's desired liquidity
       ...
   );
   ```
3. Inside `beforeAddLiquidity`, `msg.sender` is the pool, `owner` is `Alice` → `allowedDepositor[pool][Alice]` is `true` → no revert.
4. Bob's tokens enter the pool; Alice holds the LP shares.
5. Alice calls `removeLiquidity` and returns the proceeds to Bob out-of-band.
6. The allowlist has been fully bypassed with no privileged access.

---

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-98)
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
```

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
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

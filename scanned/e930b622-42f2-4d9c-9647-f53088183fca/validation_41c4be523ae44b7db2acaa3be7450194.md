### Title
`DepositAllowlistExtension` checks LP-position `owner` instead of actual depositor `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook, however, validates the caller-supplied `owner` argument (the LP-position holder) rather than the `sender` argument (the actual `msg.sender` of the pool call, i.e., the real depositor). Because `owner` is freely chosen by the caller, any address that is not on the allowlist can deposit tokens into a restricted pool by nominating an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` parameter and passes both `msg.sender` (as `sender`) and `owner` to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes both arguments in the call to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the first argument (`sender`) and checks only `owner`: [3](#0-2) 

Compare this with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual swapper): [4](#0-3) 

The asymmetry is the root cause. Because `owner` is an arbitrary address supplied by the caller, any non-allowlisted address can pass the guard by nominating an allowlisted address as the LP-position recipient.

---

### Impact Explanation

- The deposit allowlist invariant — "only allowlisted addresses may add liquidity" — is broken for every pool that configures `DepositAllowlistExtension`.
- An unauthorized depositor (Bob) can inject tokens into a restricted pool. The pool's token balances and bin accounting are updated as if a legitimate deposit occurred.
- The LP shares are credited to the nominated `owner` (Alice), not to Bob, so Bob loses tokens with no direct gain. However, the pool admin's access-control policy is fully defeated: the pool receives liquidity from an unauthorized source, which may violate KYC/compliance requirements, whitelist-only launch mechanics, or any other reason the allowlist was configured.
- Alice receives LP shares she never requested; she can remove them, but the unauthorized deposit has already altered pool state.

---

### Likelihood Explanation

- Exploitable by any external address with enough tokens to satisfy the callback.
- No privileged role, no special setup, and no malicious initial pool configuration is required — the attacker only needs to know one allowlisted address (publicly readable from `allowedDepositor`).
- The pool's `addLiquidity` function is not paused by default, so the path is always reachable.

---

### Recommendation

Change `DepositAllowlistExtension.beforeAddLiquidity` to check `sender` (the actual depositor / `msg.sender` of the pool call) instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

```solidity
// current (wrong): checks owner
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) { ... }
}

// fixed: check sender (first argument)
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) { ... }
}
``` [3](#0-2) 

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  allowedDepositor[pool][bob]   = false  (bob is NOT allowlisted)

Attack:
  bob calls pool.addLiquidity(
      owner        = alice,   // allowlisted address
      salt         = 0,
      deltas       = <valid bins/shares>,
      callbackData = <bob pays tokens in metricOmmSwapCallback>,
      extensionData = ""
  )

Extension check (beforeAddLiquidity):
  msg.sender = pool  (valid pool, passes onlyPool)
  owner      = alice
  allowedDepositor[pool][alice] == true  → guard passes

Result:
  - Bob's tokens enter the pool (unauthorized deposit succeeds)
  - Alice receives LP shares she did not request
  - Deposit allowlist is bypassed
``` [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L12-42)
```text
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }

  function setAllowAllDepositors(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllDepositors[pool_] = allowed;
    emit AllowAllDepositorsSet(pool_, allowed);
  }

  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
  }

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

### Title
`DepositAllowlistExtension` gates `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual caller and token payer) and checks only `owner` (the position holder). Because `MetricOmmPool.addLiquidity` explicitly supports the operator pattern — where `msg.sender` pays but `owner` holds the position — any actor not on the allowlist can bypass the gate by calling `addLiquidity` with `owner` set to an allowlisted address.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes both `msg.sender` (the payer) and the caller-supplied `owner` (the position holder) into the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both values to every configured extension: [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, however, the first parameter (`sender`) is unnamed and silently dropped. The allowlist lookup is keyed only on `owner`: [3](#0-2) 

Because `owner` is a free caller-supplied argument to `addLiquidity`, any unprivileged actor can set it to any allowlisted address. The extension then sees an allowlisted `owner`, passes the check, and the pool mints shares to that address while the unauthorized caller pays via the modify-liquidity callback.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual initiator), not `recipient`: [4](#0-3) 

The inconsistency is structural: the deposit extension gates the wrong identity.

The pool interface documentation acknowledges the operator pattern explicitly: [5](#0-4) 

The project's own audit-target manifest flags this exact separation as the attack surface: [6](#0-5) 

---

### Impact Explanation

The pool admin's deposit allowlist — the only on-chain mechanism restricting who may add liquidity — is fully bypassed by any unprivileged direct caller. Consequences:

1. **Unauthorized pool participation**: A non-allowlisted actor deposits tokens into a restricted pool, violating the admin's access-control intent (regulatory, risk, or partner-only pools).
2. **Pool-state manipulation**: The unauthorized depositor can choose any bin, any share amount, and any salt, directly altering bin token balances and the price cursor in a pool the admin intended to be controlled.
3. **Forced positions on allowlisted addresses**: The unauthorized actor creates positions under an allowlisted address's key; that address must then manage or withdraw unexpected positions.
4. **Admin-boundary break**: The pool admin's configured guard is bypassed by an unprivileged path (direct `pool.addLiquidity` call), satisfying the contest's admin-boundary-break impact criterion.

---

### Likelihood Explanation

- **Trigger**: A direct call to `pool.addLiquidity(owner=<any_allowlisted_address>, ...)`. No privileged access, no special token, no malicious setup required.
- **Information needed**: The address of any allowlisted depositor — typically the pool admin or a known LP, both publicly discoverable on-chain via `AllowedToDepositSet` events or `allowedDepositor` view.
- **Router path does not help**: `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` forces `owner = msg.sender`, so the bypass requires a direct pool call — but that is always available to any EOA or contract.

Likelihood: **Medium** (requires one known allowlisted address; direct pool call is always reachable).

---

### Recommendation

Check `sender` (the actual payer/initiator) instead of `owner` in `DepositAllowlistExtension.beforeAddLiquidity`, mirroring the pattern used by `SwapAllowlistExtension`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol

function beforeAddLiquidity(address sender, address /*owner*/, uint80, LiquidityDelta calldata, bytes calldata)
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

If the intent is to gate the position holder (`owner`) rather than the payer, the pool interface documentation and the `SwapAllowlistExtension` pattern should be updated to reflect that design choice explicitly, and the operator-pattern separation should be documented as a known allowlist bypass vector.

---

### Proof of Concept

```
Setup
─────
1. Deploy MetricOmmPool with DepositAllowlistExtension configured on BEFORE_ADD_LIQUIDITY_ORDER.
2. Pool admin calls extension.setAllowedToDeposit(pool, alice, true).
   → alice is the only allowlisted depositor.
3. bob is NOT on the allowlist.

Attack
──────
4. bob calls pool.addLiquidity(
       owner  = alice,   // allowlisted address
       salt   = 42,
       deltas = { binIdxs: [4], shares: [10_000] },
       callbackData  = <bob's callback pays tokens>,
       extensionData = ""
   );

Extension check (DepositAllowlistExtension.beforeAddLiquidity)
──────────────────────────────────────────────────────────────
   sender = bob   ← silently discarded (unnamed parameter)
   owner  = alice ← checked: allowedDepositor[pool][alice] == true → PASS

Result
──────
5. bob's callback transfers token0 to the pool.
6. Pool mints 10_000 shares to position key (alice, 42, bin 4).
7. bob has deposited into a pool he is not authorized to touch.
8. alice holds an unexpected position she did not create.
9. bob has manipulated bin 4's token balance in a restricted pool.

Verification: repeat with bob as owner → NotAllowedToDeposit() reverts,
confirming the gate works only when the unauthorized actor is the owner,
not when they are the payer.
```

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-162)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
  /// @param salt Namespace byte width for the key (`uint80`).
  /// @param deltas Parallel `binIdxs` / `shares` arrays (see `LiquidityDelta`).
  /// @param callbackData Opaque bytes forwarded unmodified to the modify-liquidity callback.
  /// @param extensionData Opaque bytes forwarded to liquidity extensions (beforeAddLiquidity / afterAddLiquidity).
  /// @return amount0Added Total token0 actually pulled from the callback into the pool (native).
  /// @return amount1Added Total token1 actually pulled from the callback into the pool (native).
  /// @dev Reverts `LiquidityDeltaLengthMismatch` when `binIdxs` and `shares` lengths differ.
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external returns (uint256 amount0Added, uint256 amount1Added);
```

**File:** generate_scanned_questions.py (L647-654)
```python
            short="deposit allowlist gate",
            file_function="metric-periphery/contracts/extensions/DepositAllowlistExtension.sol::beforeAddLiquidity",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::addLiquidity and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*",
            call_path="public liquidity flow -> beforeAddLiquidity hook -> allowAll/allowedDepositor lookup keyed by pool and owner",
            values="the identity actually checked against the allowlist and whether a disallowed depositor can still mint LP shares",
            control_hint="The attacker can separate payer from owner and can route through the liquidity adder, so the checked identity has to be exactly the one the pool intends to gate.",
            validation_focus="Exercise direct pool adds and liquidity-adder adds with mismatched owner/payer pairs and assert the allowlist always gates the economically relevant depositor.",
        ),
```

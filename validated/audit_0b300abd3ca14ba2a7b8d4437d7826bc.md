### Title
Fee-on-Transfer Token Deposit Inflates User Collateral Balance — (`File: core/contracts/EndpointStorage.sol`)

---

### Summary

`handleDepositTransfer` in `EndpointStorage.sol` performs a two-hop token transfer (user → Endpoint → Clearinghouse) using the caller-supplied `amount` for both hops, without measuring the actual tokens received. The slow-mode deposit transaction is then queued with the original nominal `amount`, and `Clearinghouse.depositCollateral` credits that same nominal `amount` to the user's subaccount balance. For any fee-on-transfer (deflationary) ERC20 token, the Clearinghouse receives fewer tokens than it credits, creating an unbounded accounting shortfall that can be exploited to drain other depositors' collateral.

---

### Finding Description

The deposit flow is:

1. **`Endpoint.depositCollateral` / `depositCollateralWithReferral`** accepts a caller-supplied `amount` and calls `handleDepositTransfer(token, msg.sender, uint256(amount))`. [1](#0-0) 

2. **`EndpointStorage.handleDepositTransfer`** executes two sequential transfers, both using the original `amount`:

```
safeTransferFrom(token, from, amount);          // user → Endpoint
safeTransferTo(token, address(clearinghouse), amount); // Endpoint → Clearinghouse
``` [2](#0-1) 

3. A `SlowModeTx` is queued encoding `DepositCollateral { sender, productId, amount }` — the **original nominal `amount`**, not the actual received amount. [3](#0-2) 

4. When the sequencer processes the slow-mode tx, **`Clearinghouse.depositCollateral`** credits `txn.amount` (the original nominal value) to the user's spot balance:

```solidity
int128 amountRealized = int128(txn.amount) * int128(multiplier);
spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
``` [4](#0-3) 

For a fee-on-transfer token with fee rate `f`:
- Hop 1 (user → Endpoint): Endpoint receives `amount × (1 − f)`.
- Hop 2 (Endpoint → Clearinghouse): Clearinghouse receives `amount × (1 − f)` (fee taken again from the `amount` sent).
- User is credited `amount` in full.

The Clearinghouse's real token balance is `amount × (1 − f)` per deposit, but the protocol's internal accounting records `amount`. The gap of `amount × f` per deposit is a permanent shortfall that accumulates across all depositors.

---

### Impact Explanation

An attacker deposits a fee-on-transfer token repeatedly. Each deposit credits the full nominal `amount` to their subaccount while the Clearinghouse only holds `amount × (1 − f)`. The attacker's credited balance exceeds the real on-chain token balance held by the Clearinghouse. When the attacker withdraws, `withdrawCollateral` sends the full credited amount to the attacker, draining tokens that belong to other depositors. The last depositors to withdraw find the Clearinghouse insolvent for that token. This is a direct theft of other users' collateral. [5](#0-4) 

---

### Likelihood Explanation

Any ERC20 token with a transfer fee (e.g., tokens with a built-in burn-on-transfer or redistribution mechanism) that is listed as a supported spot product triggers this path. The entry point (`depositCollateral` / `depositCollateralWithReferral`) is fully permissionless — any user can call it. No privileged access, governance capture, or external compromise is required. The attacker only needs to hold a supported fee-on-transfer token. [6](#0-5) 

---

### Recommendation

Replace the two-hop transfer with a balance-delta measurement to determine the actual amount received by the Clearinghouse. Credit only the measured received amount in the queued `DepositCollateral` transaction:

```solidity
function handleDepositTransfer(IERC20Base token, address from, uint256 amount) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    uint256 before = token.balanceOf(address(clearinghouse));
    safeTransferFrom(token, from, amount);
    safeTransferTo(token, address(clearinghouse), amount);
    uint256 actualReceived = token.balanceOf(address(clearinghouse)) - before;
    // pass actualReceived (not amount) into the SlowModeTx
}
```

Alternatively, transfer directly from the user to the Clearinghouse in a single hop, eliminating the intermediate Endpoint balance and one fee deduction. Additionally, consider explicitly disallowing fee-on-transfer tokens as supported collateral assets.

---

### Proof of Concept

1. A fee-on-transfer token `FTT` with 1% burn-on-transfer is listed as a supported spot product (productId = X).
2. Attacker calls `Endpoint.depositCollateral(subaccountName, X, 1000e18)`.
3. `handleDepositTransfer` executes:
   - `safeTransferFrom(FTT, attacker, Endpoint, 1000e18)` → Endpoint receives `990e18`.
   - `safeTransferTo(FTT, Endpoint, Clearinghouse, 1000e18)` → Clearinghouse receives `990e18` (Endpoint's balance is drawn down by `1000e18`, but Clearinghouse only gets `990e18` after the second fee).
4. `SlowModeTx` is queued with `amount = 1000e18`.
5. Sequencer processes the tx; `Clearinghouse.depositCollateral` credits attacker with `1000e18` (scaled).
6. Attacker calls `withdrawCollateral` for `1000e18`; Clearinghouse sends `1000e18` to attacker.
7. Clearinghouse is now short `10e18` tokens per deposit cycle, permanently insolvent for `FTT`. [2](#0-1) [7](#0-6)

### Citations

**File:** core/contracts/Endpoint.sol (L103-121)
```text
    function depositCollateral(
        bytes12 subaccountName,
        uint32 productId,
        uint128 amount
    ) external {
        bytes32 subaccount = bytes32(
            abi.encodePacked(msg.sender, subaccountName)
        );
        require(
            isValidDepositAmount(subaccount, productId, amount),
            ERR_DEPOSIT_TOO_SMALL
        );
        depositCollateralWithReferral(
            subaccount,
            productId,
            amount,
            DEFAULT_REFERRAL_CODE
        );
    }
```

**File:** core/contracts/Endpoint.sol (L144-148)
```text
        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
```

**File:** core/contracts/Endpoint.sol (L152-165)
```text
        slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: abi.encodePacked(
                uint8(TransactionType.DepositCollateral),
                abi.encode(
                    DepositCollateral({
                        sender: subaccount,
                        productId: productId,
                        amount: amount
                    })
                )
            )
        });
```

**File:** core/contracts/EndpointStorage.sol (L111-119)
```text
    function handleDepositTransfer(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal {
        require(address(token) != address(0), ERR_INVALID_PRODUCT);
        safeTransferFrom(token, from, amount);
        safeTransferTo(token, address(clearinghouse), amount);
    }
```

**File:** core/contracts/Clearinghouse.sol (L199-208)
```text
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        uint8 decimals = _decimals(txn.productId);

        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(txn.amount) * int128(multiplier);

        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
```

**File:** core/contracts/Clearinghouse.sol (L391-421)
```text
    function withdrawCollateral(
        bytes32 sender,
        uint32 productId,
        uint128 amount,
        address sendTo,
        uint64 idx
    ) public virtual onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(spotEngine.getConfig(productId).token);
        require(address(token) != address(0));

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
    }
```

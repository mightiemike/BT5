### Title
Depositor Address Mismatch in `depositCollateralWithReferral` Allows Permanent Fund Loss — (File: `core/contracts/Endpoint.sol`)

---

### Summary

The `depositCollateralWithReferral` function in `Endpoint.sol` accepts an arbitrary `bytes32 subaccount` parameter without verifying that `msg.sender` matches the address embedded in that parameter. Tokens are pulled from `msg.sender`, but the protocol credit is recorded against the caller-supplied `subaccount`. When these two identities diverge — a realistic outcome for account-abstraction wallet users whose on-chain address differs from the address they intend to credit — the depositor permanently loses their funds with no recovery path.

---

### Finding Description

`depositCollateralWithReferral` is a `public` function callable by any EOA or contract:

```solidity
// core/contracts/Endpoint.sol  lines 123-167
function depositCollateralWithReferral(
    bytes32 subaccount,
    uint32 productId,
    uint128 amount,
    string memory
) public {
    require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);

    address sender = address(bytes20(subaccount));   // ← identity from parameter

    requireUnsanctioned(msg.sender);
    requireUnsanctioned(sender);
    ...
    handleDepositTransfer(
        IERC20Base(spotEngine.getToken(productId)),
        msg.sender,                                  // ← tokens pulled from caller
        uint256(amount)
    );

    slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
        executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY,
        sender: sender,                              // ← stored as subaccount owner
        tx: abi.encodePacked(
            uint8(TransactionType.DepositCollateral),
            abi.encode(DepositCollateral({
                sender: subaccount,                  // ← credited subaccount
                productId: productId,
                amount: amount
            }))
        )
    });
``` [1](#0-0) 

There is no assertion that `msg.sender == address(bytes20(subaccount))`. The tokens are transferred from `msg.sender`, but the slow-mode deposit transaction is queued for `subaccount` — an entirely independent identity.

When the slow-mode transaction is later executed via `processSlowModeTransactionImpl`, the only identity check performed is `validateSender(txn.sender, sender)`, which verifies internal consistency between the stored `SlowModeTx.sender` and the decoded `DepositCollateral.sender` — it does **not** re-verify the original `msg.sender`:

```solidity
// core/contracts/EndpointTx.sol  lines 209-216
if (txType == IEndpoint.TransactionType.DepositCollateral) {
    IEndpoint.DepositCollateral memory txn = abi.decode(
        transaction[1:], (IEndpoint.DepositCollateral)
    );
    validateSender(txn.sender, sender);   // checks stored sender == subaccount prefix
    _recordSubaccount(txn.sender);
    clearinghouse.depositCollateral(txn);
``` [2](#0-1) 

```solidity
// core/contracts/EndpointTx.sol  lines 17-23
function validateSender(bytes32 txSender, address sender) internal view {
    require(
        address(uint160(bytes20(txSender))) == sender ||
            sender == address(this),
        ERR_SLOW_MODE_WRONG_SENDER
    );
}
``` [3](#0-2) 

The original `msg.sender` who paid the tokens is never re-checked at execution time.

By contrast, the simpler `depositCollateral` entry point correctly derives the subaccount from `msg.sender`, so the two identities are always aligned:

```solidity
// core/contracts/Endpoint.sol  lines 103-121
function depositCollateral(
    bytes12 subaccountName,
    uint32 productId,
    uint128 amount
) external {
    bytes32 subaccount = bytes32(
        abi.encodePacked(msg.sender, subaccountName)  // ← identity always matches payer
    );
``` [4](#0-3) 

The vulnerability exists exclusively in the `public` `depositCollateralWithReferral` path.

---

### Impact Explanation

A user who calls `depositCollateralWithReferral` with a `subaccount` whose embedded address differs from their own `msg.sender`:

- Transfers real ERC-20 tokens to the clearinghouse (irreversible on-chain transfer).
- Has those tokens credited to a subaccount they do not control and cannot sign for.
- Has no protocol-level recovery mechanism: there is no cancel-deposit function, no refund path, and no admin override for individual deposits.

The only theoretical recovery would require the unintended subaccount owner to voluntarily withdraw and return the funds — an off-protocol social action with no enforcement.

**Corrupted asset delta**: `amount` tokens are permanently removed from `msg.sender`'s balance and credited to an uncontrolled subaccount.

---

### Likelihood Explanation

The trigger is realistic for account-abstraction wallet users:

1. A user's AA wallet address on the current chain may differ from the address they believe they hold (e.g., counterfactual deployment with a different factory, salt, or implementation version).
2. A frontend or SDK that calls `depositCollateralWithReferral` directly (rather than `depositCollateral`) and constructs the `subaccount` bytes32 from an address sourced off-chain or from a different chain context will silently produce a mismatched subaccount.
3. The function is `public` and emits no warning when `msg.sender != address(bytes20(subaccount))`, so neither the user nor the frontend receives any indication of the mismatch at deposit time.
4. The 3-day slow-mode delay means the error is not discovered until the deposit is processed, by which point the token transfer is long settled. [5](#0-4) 

---

### Recommendation

Add an explicit identity check inside `depositCollateralWithReferral` that enforces the payer is the subaccount owner, unless the caller is a whitelisted contract (such as `DirectDepositV1`) that intentionally deposits on behalf of a fixed subaccount:

```solidity
address sender = address(bytes20(subaccount));
require(
    msg.sender == sender || isWhitelistedDepositor(msg.sender),
    "depositor must own subaccount"
);
```

Alternatively, document prominently that `depositCollateralWithReferral` does **not** enforce ownership, and route all user-facing deposit flows exclusively through `depositCollateral`, which correctly derives the subaccount from `msg.sender`.

---

### Proof of Concept

1. Alice holds address `A` and calls `depositCollateralWithReferral` with `subaccount = bytes32(abi.encodePacked(B, name))` where `B` is any address she does not control (e.g., her AA wallet's address on a different chain, or a mistyped address).
2. `handleDepositTransfer` pulls `amount` tokens from Alice (`msg.sender = A`) and sends them to the clearinghouse.
3. A `SlowModeTx` is queued with `sender = B` and `DepositCollateral.sender = bytes32(B||name)`.
4. After the 3-day delay, the slow-mode transaction executes: `_recordSubaccount(bytes32(B||name))` and `clearinghouse.depositCollateral(...)` credit `B`'s subaccount.
5. Alice's tokens are gone. She has no subaccount entry, no signed transaction capability over `B`'s subaccount, and no refund path in the protocol. [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/Endpoint.sol (L103-110)
```text
    function depositCollateral(
        bytes12 subaccountName,
        uint32 productId,
        uint128 amount
    ) external {
        bytes32 subaccount = bytes32(
            abi.encodePacked(msg.sender, subaccountName)
        );
```

**File:** core/contracts/Endpoint.sol (L123-167)
```text
    function depositCollateralWithReferral(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount,
        string memory
    ) public {
        require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);

        address sender = address(bytes20(subaccount));

        // depositor / depositee need to be unsanctioned
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);

        if (!isValidDepositAmount(subaccount, productId, amount)) {
            // we cannot revert here, otherwise direct deposit could be blocked when there are
            // multiple assets awaiting credit but one of them is below the minimum deposit amount.
            // we can just skip the deposit and continue with the next asset.
            return;
        }

        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
        // copy from submitSlowModeTransaction
        SlowModeConfig memory _slowModeConfig = slowModeConfig;

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
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/EndpointTx.sol (L17-23)
```text
    function validateSender(bytes32 txSender, address sender) internal view {
        require(
            address(uint160(bytes20(txSender))) == sender ||
                sender == address(this),
            ERR_SLOW_MODE_WRONG_SENDER
        );
    }
```

**File:** core/contracts/EndpointTx.sol (L209-216)
```text
        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            IEndpoint.DepositCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositCollateral)
            );
            validateSender(txn.sender, sender);
            _recordSubaccount(txn.sender);
            clearinghouse.depositCollateral(txn);
```

**File:** core/contracts/EndpointStorage.sol (L67-72)
```text
    function _recordSubaccount(bytes32 subaccount) internal {
        if (subaccountIds[subaccount] == 0) {
            subaccountIds[subaccount] = ++numSubaccounts;
            subaccounts[numSubaccounts] = subaccount;
        }
    }
```

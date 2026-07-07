### Title
Off-Chain Snapshot Gaming via Flash Deposits Enables Disproportionate Airdrop Claims — (File: `core/contracts/Airdrop.sol`, `core/contracts/Endpoint.sol`)

---

### Summary

The `Airdrop.sol` contract distributes weekly token rewards based on off-chain Merkle snapshots of user balances. Because `Endpoint.depositCollateral` allows instant, unconstrained deposits with no minimum holding period, and because the sequencer-processed `WithdrawCollateral` path enforces no on-chain time delay, an unprivileged user can flash-deposit a large amount immediately before the weekly snapshot, appear as a large depositor in the Merkle root, and withdraw immediately after — claiming disproportionate airdrop rewards while only holding funds for a negligible duration.

---

### Finding Description

`Airdrop.sol` distributes rewards weekly. The owner calls `registerMerkleRoot` to register a new Merkle root derived from an off-chain snapshot of user balances at a recurring timestamp. [1](#0-0) 

The snapshot reads on-chain state — specifically, user collateral balances tracked in `SpotEngine`. The entry point for increasing that on-chain balance is `Endpoint.depositCollateral`, which is directly callable by any user with no holding-period requirement: [2](#0-1) 

The deposit is enqueued as a slow-mode transaction with a 3-day delay for *processing by the clearinghouse*, but the funds are taken into custody immediately and the balance is credited once the sequencer processes the `DepositCollateral` slow-mode entry. Critically, the sequencer-processed `WithdrawCollateral` path in `EndpointTx.processTransactionImpl` calls `clearinghouse.withdrawCollateral` with no on-chain time-lock: [3](#0-2) 

This means a user who deposits before the snapshot and submits a signed `WithdrawCollateral` to the sequencer after the snapshot can recover their funds in the same sequencer batch — with no on-chain enforcement preventing the round-trip.

The `Airdrop.claim` function then pays out based solely on the Merkle proof for the snapshot week, with no on-chain check of how long the user actually held their balance: [4](#0-3) 

The corrupted on-chain state is `claimed[week][attacker]` being set to an inflated `totalAmount` that the attacker does not legitimately deserve, draining tokens from the airdrop pool. [5](#0-4) 

---

### Impact Explanation

An attacker who successfully times a flash deposit around the weekly snapshot claims a larger share of the fixed weekly airdrop token pool. Legitimate long-term depositors receive proportionally less. The asset delta is direct: airdrop tokens are transferred to the attacker via `SafeERC20.safeTransfer` in `_claim`, reducing the pool available to genuine participants. [6](#0-5) 

---

### Likelihood Explanation

The weekly snapshot cadence is predictable: `pastWeeks` increments monotonically with each `registerMerkleRoot` call, and the weekly rhythm is publicly observable on-chain. An attacker can monitor mempool or block timestamps to anticipate the snapshot window. The attack is profitable whenever the airdrop value captured exceeds the gas cost of the deposit + withdrawal round-trip, which is realistic for any non-trivial reward pool. [7](#0-6) 

---

### Recommendation

1. **Time-weighted average balances**: Compute snapshot rewards using a time-weighted average balance (TWAB) over the full week rather than a point-in-time snapshot, so a flash deposit contributes negligibly to the reward calculation.
2. **Minimum holding period**: Introduce an on-chain minimum deposit age before a balance qualifies for snapshot inclusion, enforced at the contract level rather than relying solely on off-chain snapshot logic.
3. **Snapshot time variability**: Introduce unpredictability in the exact snapshot timestamp (e.g., using a VRF or randomized offset within the weekly window) to prevent precise timing attacks.

---

### Proof of Concept

1. Monitor `registerMerkleRoot` call history to determine the weekly snapshot cadence and predict the next snapshot block.
2. Call `Endpoint.depositCollateral` with a large amount of collateral immediately before the predicted snapshot block. [2](#0-1) 

3. The sequencer processes the `DepositCollateral` slow-mode entry, crediting the balance in `SpotEngine` before the off-chain snapshot is taken.
4. The off-chain snapshot captures the inflated balance; the owner registers the new Merkle root via `registerMerkleRoot` with the attacker included at the inflated amount.
5. Submit a signed `WithdrawCollateral` transaction to the sequencer immediately after the snapshot. The sequencer processes it via `processTransactionImpl` with no on-chain time-lock, returning the collateral. [3](#0-2) 

6. Call `Airdrop.claim` with the Merkle proof for the inflated balance, receiving disproportionate airdrop tokens. [8](#0-7)

### Citations

**File:** core/contracts/Airdrop.sol (L33-40)
```text
    function registerMerkleRoot(uint32 week, bytes32 merkleRoot)
        external
        onlyOwner
    {
        pastWeeks += 1;
        require(week == pastWeeks, "Invalid week provided.");
        merkleRoots[week] = merkleRoot;
    }
```

**File:** core/contracts/Airdrop.sol (L62-62)
```text
        claimed[week][sender] = totalAmount;
```

**File:** core/contracts/Airdrop.sol (L65-73)
```text
    function _claim(
        uint32 week,
        uint256 totalAmount,
        bytes32[] calldata proof
    ) internal {
        _verifyProof(week, msg.sender, totalAmount, proof);
        SafeERC20.safeTransfer(IERC20(token), msg.sender, totalAmount);
        emit Claim(msg.sender, week, totalAmount);
    }
```

**File:** core/contracts/Airdrop.sol (L75-83)
```text
    function claim(ClaimProof[] calldata claimProofs) external {
        for (uint32 i = 0; i < claimProofs.length; i++) {
            _claim(
                claimProofs[i].week,
                claimProofs[i].totalAmount,
                claimProofs[i].proof
            );
        }
    }
```

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

**File:** core/contracts/EndpointTx.sol (L413-436)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.SignedWithdrawCollateral memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateral)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(
                signedTx.tx.sender,
                spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                address(0),
                nSubmissions
            );
```

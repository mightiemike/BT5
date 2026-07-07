### Title
Unrestricted `depositCollateralWithReferral` Enables Slow-Mode Queue Griefing to Permanently Block Victim Withdrawals - (File: `core/contracts/Endpoint.sol`)

---

### Summary

`depositCollateralWithReferral` is a `public` function that allows any caller to deposit tokens to **any** subaccount without verifying ownership. An attacker can exploit this to flood the global FIFO slow-mode queue with `DepositCollateral` entries ahead of a victim's withdrawal transaction, permanently blocking the victim's access to their collateral via the slow-mode safety mechanism at a cost of only `$0.10` per queue slot.

---

### Finding Description

`depositCollateralWithReferral` in `Endpoint.sol` accepts an arbitrary `bytes32 subaccount` parameter and performs no check that `msg.sender` owns that subaccount:

```solidity
function depositCollateralWithReferral(
    bytes32 subaccount,
    uint32 productId,
    uint128 amount,
    string memory
) public {
    require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);
    address sender = address(bytes20(subaccount));
    requireUnsanctioned(msg.sender);
    requireUnsanctioned(sender);
    ...
    handleDepositTransfer(IERC20Base(spotEngine.getToken(productId)), msg.sender, uint256(amount));
    slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({...});
``` [1](#0-0) 

The only guards are: the subaccount is not isolated, and neither `msg.sender` nor the subaccount owner is sanctioned. There is no `msg.sender == address(bytes20(subaccount))` check.

Each call inserts a `DepositCollateral` slow-mode transaction into the global FIFO queue **without charging the `SLOW_MODE_FEE = $1`** that `submitSlowModeTransaction` charges. The only cost to the attacker is the minimum deposit amount (`MIN_DEPOSIT_AMOUNT = $0.10` for existing subaccounts, `MIN_FIRST_DEPOSIT_AMOUNT = $5` for new ones). [2](#0-1) 

The slow-mode queue is a strict FIFO structure processed one entry at a time via `executeSlowModeTransaction`:

```solidity
function executeSlowModeTransaction() external {
    SlowModeConfig memory _slowModeConfig = slowModeConfig;
    _executeSlowModeTransaction(_slowModeConfig, false);
    nSubmissions += 1;
    slowModeConfig = _slowModeConfig;
}
``` [3](#0-2) 

`_executeSlowModeTransaction` increments `txUpTo` by exactly one per call, consuming the head of the queue: [4](#0-3) 

Any slow-mode tx inserted at a lower queue index than the victim's withdrawal must be drained before the victim's withdrawal can execute.

---

### Impact Explanation

The slow-mode mechanism is the **safety mechanism of last resort** when the sequencer is offline — it is the only on-chain path for users to withdraw collateral without sequencer cooperation. If an attacker continuously calls `depositCollateralWithReferral` targeting a victim's subaccount, they can maintain a permanent lead in the queue ahead of any withdrawal the victim submits. The victim must call `executeSlowModeTransaction` once per attacker-inserted entry before their own withdrawal is reachable. Since the attacker can insert entries faster than the victim can drain them (at `$0.10` per slot, no slow-mode fee), the victim's collateral is effectively locked in the protocol for as long as the attacker sustains the attack. This is a permanent, low-cost DoS on the only trustless withdrawal path.

---

### Likelihood Explanation

**Medium-High.** The attack requires the sequencer to be offline — precisely the scenario where slow mode is the intended safety net. The attacker needs no special privileges, no governance access, and no leaked keys. The cost is `$0.10` per queue slot (no slow-mode fee is charged via this path). Front-running a victim's slow-mode withdrawal submission on-chain is straightforward. The attack is economically viable against any user attempting to withdraw meaningful collateral.

---

### Recommendation

Add an ownership check in `depositCollateralWithReferral` requiring that `msg.sender` is the owner of the target subaccount:

```solidity
require(
    address(bytes20(subaccount)) == msg.sender,
    ERR_UNAUTHORIZED
);
```

This mirrors the fix in the referenced report: restrict the deposit action to the subaccount owner only, eliminating the ability for a third party to inject entries into the slow-mode queue on behalf of an arbitrary subaccount.

---

### Proof of Concept

1. Sequencer goes offline. Victim prepares to submit a slow-mode `WithdrawCollateral` transaction.
2. Attacker monitors the mempool and front-runs the victim's submission by calling `depositCollateralWithReferral(victimSubaccount, quoteProductId, MIN_DEPOSIT_AMOUNT, "")` N times in rapid succession. Each call costs `$0.10` in tokens (no slow-mode fee). N entries are inserted at queue positions `[k, k+1, ..., k+N-1]`.
3. Victim's `WithdrawCollateral` slow-mode tx lands at queue position `k+N`.
4. Victim calls `executeSlowModeTransaction()` — it processes the attacker's deposit at position `k`, not the victim's withdrawal.
5. Attacker immediately calls `depositCollateralWithReferral` again, inserting a new entry at position `k+N+1`, maintaining the lead.
6. The victim can never advance past the attacker's entries. Their collateral withdrawal is permanently blocked via slow mode for as long as the attacker spends `$0.10` per block. [5](#0-4) [6](#0-5)

### Citations

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

**File:** core/contracts/Endpoint.sol (L185-236)
```text
    function _executeSlowModeTransaction(
        SlowModeConfig memory _slowModeConfig,
        bool fromSequencer
    ) internal {
        require(
            _slowModeConfig.txUpTo < _slowModeConfig.txCount,
            ERR_NO_SLOW_MODE_TXS_REMAINING
        );
        SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
        delete slowModeTxs[_slowModeConfig.txUpTo++];

        require(
            fromSequencer || (txn.executableAt <= block.timestamp),
            ERR_SLOW_TX_TOO_RECENT
        );

        if (block.chainid == 31337) {
            // for testing purposes, we don't fail silently when the chainId is hardhat's default.
            this.processSlowModeTransaction(txn.sender, txn.tx);
        } else {
            uint256 gasRemaining = gasleft();
            // solhint-disable-next-line no-empty-blocks
            try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
                // we need to differentiate between a revert and an out of gas
                // the issue is that in evm every inner call only 63/64 of the
                // remaining gas in the outer frame is forwarded. as a result
                // the amount of gas left for execution is (63/64)**len(stack)
                // and you can get an out of gas while spending an arbitrarily
                // low amount of gas in the final frame. we use a heuristic
                // here that isn't perfect but covers our cases.
                // having gasleft() <= gasRemaining / 2 buys us 44 nested calls
                // before we miss out of gas errors; 1/2 ~= (63/64)**44
                // this is good enough for our purposes

                if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
                    // solhint-disable-next-line no-inline-assembly
                    assembly {
                        invalid()
                    }
                }

                // try return funds now removed
            }
        }
    }

    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/common/Constants.sol (L23-42)
```text
int128 constant SLOW_MODE_FEE = 1000000; // $1

int128 constant FAST_WITHDRAWAL_FEE_RATE = 1_000_000_000_000_000; // 0.1%

int128 constant LIQUIDATION_FEE = 1e18; // $1
int128 constant HEALTHCHECK_FEE = 1e18; // $1

uint128 constant INT128_MAX = uint128(type(int128).max);

uint64 constant SECONDS_PER_DAY = 3600 * 24;

uint32 constant VRTX_PRODUCT_ID = 41;

int128 constant LIQUIDATION_FEE_FRACTION = 500_000_000_000_000_000; // 50%

int128 constant INTEREST_FEE_FRACTION = 200_000_000_000_000_000; // 20%

int256 constant MIN_DEPOSIT_AMOUNT = ONE / 10; // $0.1

int256 constant MIN_FIRST_DEPOSIT_AMOUNT = 5 * ONE; // $5
```

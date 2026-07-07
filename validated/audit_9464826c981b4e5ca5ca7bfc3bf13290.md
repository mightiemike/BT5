### Title
Unchecked `transferFrom` Return Value Enables usdcE Drain Without usdc Deposit — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` performs a raw `.transferFrom()` call to pull usdc from the caller without checking the return value. If the call returns `false` instead of reverting, the function continues to withdraw usdcE from the DDA and transfer it to the caller — giving the caller usdcE for free.

---

### Finding Description

`replaceUsdcEWithUsdc` is a public swap function (no access control beyond a chain-ID gate) intended to let any caller exchange usdc for usdcE held in a Direct Deposit Address (DDA). The swap logic is:

1. Read the usdcE balance of the DDA.
2. Pull `balance` usdc from `msg.sender` into the DDA via `transferFrom`.
3. Withdraw usdcE from the DDA to `ContractOwner`.
4. `safeTransfer` the usdcE to `msg.sender`.

Step 2 uses a raw, unchecked `.transferFrom()`:

```solidity
// core/contracts/ContractOwner.sol line 616
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
```

The return value is never inspected. If the token returns `false` on failure (rather than reverting), execution falls through to steps 3 and 4, which unconditionally drain the DDA's usdcE and send it to the caller.

Every other ERC20 transfer in the codebase uses the safe wrapper from `ERC20Helper` (which checks the return value and reverts on failure), or OpenZeppelin's `SafeERC20`. This call is the sole exception. [1](#0-0) 

For comparison, the safe pattern used everywhere else: [2](#0-1) 

---

### Impact Explanation

An attacker on chain 57073 (Ink) can call `replaceUsdcEWithUsdc` for any subaccount whose DDA holds usdcE. If the usdc token at `0x2D270e6886d130D724215A266106e6832161EAEd` returns `false` on a failed `transferFrom` (e.g., insufficient allowance or balance), the attacker receives the full usdcE balance of the DDA without depositing any usdc. This is a direct asset theft from DDA holders: the corrupted state delta is `DDA.usdcE -= balance` and `attacker.usdcE += balance` with no corresponding `attacker.usdc -= balance`.

---

### Likelihood Explanation

The function is externally reachable by any unprivileged caller on chain 57073 with no further preconditions. The only prerequisite is that a DDA exists with a non-zero usdcE balance. Whether the specific usdc deployment at that address reverts or returns `false` on failure determines exploitability; non-reverting ERC20 behavior is common among bridged/wrapped stablecoins. The absence of any return-value check makes this a latent critical path regardless of current token behavior, since token implementations can change.

---

### Recommendation

Replace the raw `.transferFrom()` call with the project's own `ERC20Helper.safeTransferFrom`, consistent with all other transfer sites in the codebase:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [3](#0-2) 

---

### Proof of Concept

1. A DDA for `subaccount` on chain 57073 holds `N` usdcE.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` with zero usdc allowance granted to `ContractOwner`.
3. `IERC20Base(usdc).transferFrom(attacker, dda, N)` returns `false` (no revert) due to zero allowance.
4. Return value is ignored; execution continues.
5. `DirectDepositV1(dda).withdraw(usdcE)` transfers `N` usdcE from the DDA to `ContractOwner`.
6. `IERC20Base(usdcE).safeTransfer(attacker, N)` sends `N` usdcE to the attacker.
7. Attacker holds `N` usdcE; DDA holds `0` usdcE; no usdc was ever deposited. [4](#0-3)

### Citations

**File:** core/contracts/ContractOwner.sol (L608-620)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
        address usdc = 0x2D270e6886d130D724215A266106e6832161EAEd;
        uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
        if (balance > 0) {
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
        }
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```
